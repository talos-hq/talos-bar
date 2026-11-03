//! Running the Qt bar's Python scripts as subprocesses, the way its glue does.
//!
//! `src/talos_bar/glue/builtin/tasks.py` runs `tasks_cli list` and `agenda.py` runs
//! `calendar_agenda` (which parses the iCal file through `calendar_ical`). Both honour an
//! override command first: `TALOS_BAR_TASKS_CMD <args>` and `TALOS_BAR_AGENDA_CMD`. Without one,
//! this bar runs the script file with a Python that has the scripts' dependencies
//! (`icalendar`, `markdown-it-py`): `TALOS_BAR_PYTHON` (whitespace-separated argv) or, by
//! default, `uv run --no-project --with icalendar --with markdown-it-py python`.
//!
//! A fetch runs on a worker thread and lands in a [`Feed`] the UI polls; the UI never blocks on
//! a script.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

/// The glue's own timeout for both scripts (`run(..., timeout=25)`).
pub const TIMEOUT: Duration = Duration::from_secs(25);

const DEFAULT_PYTHON: [&str; 8] =
    ["uv", "run", "-q", "--no-project", "--with", "icalendar", "--with", "markdown-it-py"];

/// Where the scripts live: `TALOS_BAR_SCRIPTS`, else this checkout's `src/talos_bar/scripts`.
pub fn scripts_dir() -> PathBuf {
    std::env::var_os("TALOS_BAR_SCRIPTS")
        .filter(|v| !v.is_empty())
        .map_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/talos_bar/scripts"), PathBuf::from)
}

/// The interpreter argv the script path is appended to.
pub fn python(env: Option<&str>) -> Vec<String> {
    match env.map(str::split_whitespace) {
        Some(words) if env.is_some_and(|v| !v.trim().is_empty()) => words.map(str::to_owned).collect(),
        _ => DEFAULT_PYTHON.iter().map(|w| (*w).to_owned()).chain(["python".to_owned()]).collect(),
    }
}

/// The argv for one script, as the glue builds it: the override command plus `args`, or the
/// interpreter, the script file and `args`.
pub fn command(override_cmd: Option<&str>, python: &[String], script: &Path, args: &[&str]) -> Vec<String> {
    let extra = args.iter().map(|a| (*a).to_owned());
    match override_cmd.filter(|c| !c.is_empty()) {
        Some(cmd) => std::iter::once(cmd.to_owned()).chain(extra).collect(),
        None => python.iter().cloned().chain([script.display().to_string()]).chain(extra).collect(),
    }
}

/// `tasks_cli.py <args>`.
pub fn tasks_command_with(args: &[&str]) -> Vec<String> {
    let py = python(std::env::var("TALOS_BAR_PYTHON").ok().as_deref());
    let cmd = std::env::var("TALOS_BAR_TASKS_CMD").ok();
    command(cmd.as_deref(), &py, &scripts_dir().join("tasks_cli.py"), args)
}

/// `tasks_cli.py list`.
pub fn tasks_command() -> Vec<String> {
    tasks_command_with(&["list"])
}

/// Runs `tasks_cli.py` with arguments and returns its stdout; tests inject a fake.
pub type Cli = Arc<dyn Fn(&[&str]) -> Result<String, String> + Send + Sync>;

/// The real `tasks_cli.py`, with the glue's override and timeout.
pub fn tasks_cli() -> Cli {
    Arc::new(|args| run(&tasks_command_with(args), TIMEOUT))
}

/// Whether a job runs, and the last finished job's result until it is taken.
type JobState = (bool, Option<Result<String, String>>);

/// One background job at a time (a task edit): started from the UI, polled by it.
#[derive(Clone, Default)]
pub struct Job {
    state: Arc<Mutex<JobState>>,
}

impl Job {
    /// Run `work` on a thread; refused (false) while another job runs.
    pub fn start(&self, work: impl FnOnce() -> Result<String, String> + Send + 'static) -> bool {
        {
            let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            if state.0 {
                return false;
            }
            *state = (true, None);
        }
        let state = Arc::clone(&self.state);
        std::thread::spawn(move || {
            let result = work();
            *state.lock().unwrap_or_else(PoisonError::into_inner) = (false, Some(result));
        });
        true
    }

    pub fn running(&self) -> bool {
        self.state.lock().unwrap_or_else(PoisonError::into_inner).0
    }

    /// The finished job's result, once.
    pub fn take(&self) -> Option<Result<String, String>> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner).1.take()
    }
}

/// `calendar_agenda.py` (no arguments).
pub fn agenda_command() -> Vec<String> {
    let py = python(std::env::var("TALOS_BAR_PYTHON").ok().as_deref());
    let cmd = std::env::var("TALOS_BAR_AGENDA_CMD").ok();
    command(cmd.as_deref(), &py, &scripts_dir().join("calendar_agenda.py"), &[])
}

/// Run `argv` and return its stdout, failing on a non-zero exit, a spawn error or `timeout`.
/// The scripts import their siblings, so they run from the scripts directory.
pub fn run(argv: &[String], timeout: Duration) -> Result<String, String> {
    let (program, args) = argv.split_first().ok_or("empty command")?;
    let mut child = Command::new(program)
        .args(args)
        .current_dir(scripts_dir())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("{program}: {err}"))?;
    let mut stdout = child.stdout.take().ok_or("no stdout")?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut out = String::new();
        let read = stdout.read_to_string(&mut out).map(|_| out);
        let _ = tx.send(read);
    });
    let out = match rx.recv_timeout(timeout) {
        Ok(read) => read.map_err(|err| err.to_string())?,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{program}: timed out after {}s", timeout.as_secs()));
        }
    };
    let status = child.wait().map_err(|err| err.to_string())?;
    if !status.success() {
        let mut err = String::new();
        if let Some(mut stderr) = child.stderr.take() {
            let _ = stderr.read_to_string(&mut err);
        }
        let last = err.lines().last().unwrap_or("").trim().to_owned();
        return Err(format!("{program} exited with {status}: {last}"));
    }
    Ok(out)
}

/// Produces a script's raw output; the real one runs [`run`], tests inject canned text.
pub type Fetch = Arc<dyn Fn() -> Result<String, String> + Send + Sync>;

pub fn script(argv: Vec<String>) -> Fetch {
    Arc::new(move || run(&argv, TIMEOUT))
}

#[derive(Debug, Default)]
struct State<T> {
    value: Option<Result<T, String>>,
    loading: bool,
    fetched: Option<Instant>,
}

/// The latest parsed result of one script, shared between the UI and its fetch thread.
pub struct Feed<T> {
    state: Arc<Mutex<State<T>>>,
    fetch: Fetch,
    parse: fn(&str) -> Result<T, String>,
}

impl<T: Clone + Send + 'static> Feed<T> {
    pub fn new(fetch: Fetch, parse: fn(&str) -> Result<T, String>) -> Self {
        Self { state: Arc::new(Mutex::new(State { value: None, loading: false, fetched: None })), fetch, parse }
    }

    /// Start a fetch unless one is running.
    pub fn refresh(&self) {
        {
            let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            if state.loading {
                return;
            }
            state.loading = true;
        }
        let (state, fetch, parse) = (Arc::clone(&self.state), Arc::clone(&self.fetch), self.parse);
        std::thread::spawn(move || {
            let value = fetch().and_then(|raw| parse(&raw));
            let mut state = state.lock().unwrap_or_else(PoisonError::into_inner);
            state.value = Some(value);
            state.loading = false;
            state.fetched = Some(Instant::now());
        });
    }

    /// Refresh when the last fetch is older than `every` (or there was none).
    pub fn refresh_if_older(&self, every: Duration) {
        let stale = {
            let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            !state.loading && state.fetched.is_none_or(|at| at.elapsed() >= every)
        };
        if stale {
            self.refresh();
        }
    }

    /// The last result (`None` before the first fetch finishes) and whether a fetch is running.
    pub fn get(&self) -> (Option<Result<T, String>>, bool) {
        let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        (state.value.clone(), state.loading)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn override_command_wins_like_the_glue() {
        let py = python(None);
        let argv = command(Some("/bin/mytasks"), &py, Path::new("/s/tasks_cli.py"), &["list"]);
        assert_eq!(argv, ["/bin/mytasks", "list"]);
        let argv = command(Some(""), &py, Path::new("/s/tasks_cli.py"), &["list"]);
        assert_eq!(argv.last().map(String::as_str), Some("list"));
        assert_eq!(argv[argv.len() - 2], "/s/tasks_cli.py");
        assert_eq!(argv[0], "uv");
    }

    #[test]
    fn python_env_is_split_on_whitespace() {
        assert_eq!(python(Some("/nix/py/bin/python3 -I")), ["/nix/py/bin/python3", "-I"]);
        assert_eq!(python(Some("  ")).last().map(String::as_str), Some("python"));
    }

    #[test]
    fn run_reports_exit_and_output() {
        let ok = run(&["sh".into(), "-c".into(), "echo hi".into()], TIMEOUT).expect("runs");
        assert_eq!(ok, "hi\n");
        let err = run(&["sh".into(), "-c".into(), "echo boom >&2; exit 3".into()], TIMEOUT).expect_err("fails");
        assert!(err.contains("boom"), "{err}");
        let err = run(&["sh".into(), "-c".into(), "sleep 5".into()], Duration::from_millis(50)).expect_err("times out");
        assert!(err.contains("timed out"), "{err}");
        assert!(run(&["/nonexistent/x".into()], TIMEOUT).is_err());
    }

    #[test]
    fn feed_fetches_on_a_thread_and_parses() {
        let feed: Feed<usize> = Feed::new(Arc::new(|| Ok("abc".to_owned())), |raw| Ok(raw.len()));
        assert_eq!(feed.get(), (None, false));
        feed.refresh();
        let deadline = Instant::now() + Duration::from_secs(5);
        let value = loop {
            match feed.get() {
                (Some(v), false) => break v,
                _ if Instant::now() > deadline => panic!("fetch never finished"),
                _ => std::thread::yield_now(),
            }
        };
        assert_eq!(value, Ok(3));
    }

    #[test]
    fn a_job_runs_once_at_a_time_and_reports_once() {
        let job = Job::default();
        let (tx, rx) = mpsc::channel::<()>();
        assert!(job.start(move || {
            let _ = rx.recv();
            Err("boom".to_owned())
        }));
        assert!(job.running());
        assert!(!job.start(|| Ok(String::new())), "a second job is refused while one runs");
        tx.send(()).expect("release");
        let deadline = Instant::now() + Duration::from_secs(5);
        while job.running() {
            assert!(Instant::now() < deadline, "job never finished");
            std::thread::yield_now();
        }
        assert_eq!(job.take(), Some(Err("boom".to_owned())));
        assert_eq!(job.take(), None);
    }
}
