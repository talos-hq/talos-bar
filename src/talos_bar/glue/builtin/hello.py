"""Example glue: edit this file while the bar runs and it reloads."""

from talos_bar.glue import Context, Output, every, item, on_click


@item("hello", label="Hello")
@every(seconds=5)
def refresh(ctx: Context) -> Output:
    clicks: int = ctx.cache.get("clicks", 0)
    return f"glue ok ({clicks} clicks)"


@on_click("hello")
def click(ctx: Context) -> None:
    ctx.cache["clicks"] = ctx.cache.get("clicks", 0) + 1
