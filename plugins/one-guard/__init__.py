from .guard import Guard


def register(ctx):
    guard = Guard(ctx)
    ctx.register_hook("pre_tool_call", guard.pre_tool_call)
