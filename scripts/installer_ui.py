import getpass
import os
import sys
import unicodedata


BORDER = "─"
HINT = "↑/↓ or j/k to move · Enter to select · Esc to cancel"


def safe_text(value):
    return "".join(char if not unicodedata.category(char).startswith("C") else
                   f"\\u{ord(char):04x}" for char in str(value))


def rich_available():
    try:
        import prompt_toolkit
    except ImportError:
        return False
    return True


def wants_rich(args):
    if getattr(args, "plain", False) or getattr(args, "yes", False):
        return False
    if os.environ.get("TERM", "dumb") == "dumb":
        return False
    if "NO_COLOR" in os.environ:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


class PlainUI:
    def __init__(self, input=None, output=None):
        self.secrets = []
        self.input = input
        self.output = output

    def clean(self, value):
        text = str(value)
        for secret in self.secrets:
            if isinstance(secret, str) and secret:
                text = text.replace(secret, "[credential]")
        return safe_text(text)
    def say(self, message=""):
        print(self.clean(message), flush=True)

    def header(self, title, description=""):
        print(flush=True)
        self.say(BORDER * 64)
        self.say(self.clean(title))
        if description:
            self.say(self.clean(description))
        self.say(BORDER * 64)

    def summary(self, rows):
        if not rows:
            return
        width = max(len(str(label)) for label, _ in rows)
        for label, value in rows:
            self.say(f"  {self.clean(label).ljust(width)}  {self.clean(value)}")

    def choose(self, title, choices, default=0):
        self.say(self.clean(title))
        for index, (_, label) in enumerate(choices, 1):
            marker = "  " if index != default + 1 else "› "
            self.say(f"  {marker}{index}. {label}")
        while True:
            try:
                answer = input(f"  Choice [{default + 1}]: ").strip()
            except EOFError:
                raise KeyboardInterrupt() from None
            if not answer:
                return choices[default][0]
            if answer.isdecimal() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1][0]
            self.say("  Enter one of the listed numbers.")

    def text(self, label, default="", validate=str, secret=False, allow_empty=False):
        while True:
            try:
                if secret:
                    answer = getpass.getpass(f"  {self.clean(label)}: ")
                else:
                    suffix = f" [{self.clean(default)}]" if default != "" else ""
                    answer = input(f"  {self.clean(label)}{suffix}: ")
                    if not answer:
                        answer = str(default)
            except EOFError:
                raise KeyboardInterrupt() from None
            if not answer and allow_empty:
                return None
            try:
                result = validate(answer)
            except (ValueError, OSError) as exc:
                self.say("  Invalid credential: use printable ASCII, at most 16384 characters, "
                         "without ${ sequences." if secret else "  " + self.clean(exc))
                continue
            if secret:
                self.secrets.append(answer)
            return result


class ToolkitUI(PlainUI):
    def __init__(self, input=None, output=None):
        super().__init__(input=input, output=output)
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.styles import Style
        self.formatted = FormattedText
        self.style = Style.from_dict({
            "title": "bold #5fd7d7",
            "selected": "bold #5fd7d7",
            "hint": "#8a8a8a",
            "error": "#ff6b6b",
            "label": "#cfcfcf",
            "value": "#e8e8e8",
            "border": "#4a4a4a",
        })

    def print(self, fragments):
        from prompt_toolkit import print_formatted_text
        print_formatted_text(self.formatted(fragments), output=self.output, style=self.style)

    def say(self, message=""):
        self.print([("", self.clean(message))])

    def header(self, title, description=""):
        fragments = [("class:border", BORDER * 64 + "\n"),
                     ("class:title", "◆ " + self.clean(title) + "\n")]
        if description:
            fragments.append(("class:hint", self.clean(description) + "\n"))
        fragments.append(("class:border", BORDER * 64))
        self.print(fragments)

    def summary(self, rows):
        if not rows:
            return
        width = max(len(str(label)) for label, _ in rows)
        fragments = []
        for label, value in rows:
            fragments.append(("class:label", f"  {self.clean(label).ljust(width)}  "))
            fragments.append(("class:value", self.clean(value) + "\n"))
        self.print(fragments)

    def choose(self, title, choices, default=0):
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        selected = default

        def move(step):
            nonlocal selected
            selected = (selected + step) % len(choices)

        keys = KeyBindings()

        @keys.add("up")
        @keys.add("k")
        @keys.add("s-tab")
        def previous(event):
            move(-1)

        @keys.add("down")
        @keys.add("j")
        @keys.add("tab")
        def following(event):
            move(1)

        @keys.add("home")
        def first(event):
            nonlocal selected
            selected = 0

        @keys.add("end")
        def last(event):
            nonlocal selected
            selected = len(choices) - 1

        @keys.add("enter")
        def accept(event):
            event.app.exit(result=selected)

        @keys.add("c-c")
        @keys.add("escape")
        @keys.add("q")
        def cancel(event):
            event.app.exit(exception=KeyboardInterrupt())

        @keys.add("c-d")
        def eof(event):
            event.app.exit(exception=EOFError())

        for digit in "123456789":
            def jump(event, digit=digit):
                index = int(digit) - 1
                if index < len(choices):
                    event.app.exit(result=index)
            keys.add(digit)(jump)

        def render():
            fragments = [("class:title", self.clean(title) + "\n"),
                         ("class:border", BORDER * 64 + "\n")]
            for index, (_, label) in enumerate(choices):
                active = index == selected
                fragments.append(("class:selected" if active else "class:label",
                                  ("  › " if active else "    ") + self.clean(label) + "\n"))
            fragments.append(("class:border", BORDER * 64 + "\n"))
            fragments.append(("class:hint", "  " + HINT))
            return fragments

        control = FormattedTextControl(render, focusable=True)
        app = Application(layout=Layout(Window(control, wrap_lines=True)), key_bindings=keys,
                          full_screen=False, erase_when_done=True, style=self.style,
                          input=self.input, output=self.output)
        index = app.run()
        value, label = choices[index]
        self.say(f"{title}: {label}")
        return value

    def text(self, label, default="", validate=str, secret=False, allow_empty=False):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import DummyHistory
        from prompt_toolkit.validation import Validator, ValidationError
        ui = self

        class FieldValidator(Validator):
            def validate(self, document):
                if not document.text and not secret:
                    return
                try:
                    validate(document.text)
                except (ValueError, OSError) as exc:
                    message = ("Use printable ASCII, at most 16384 characters, without ${ sequences."
                               if secret else ui.clean(exc))
                    raise ValidationError(message=message, cursor_position=len(document.text)) from None

        def toolbar():
            if secret:
                return [("class:hint", "  Input is hidden and never stored in history")]
            if default != "":
                return [("class:hint", f"  Default: {ui.clean(default)}  (Enter keeps it)")]
            return [("class:hint", "  Enter to accept")]

        placeholder = None if secret else ("Default: " + self.clean(default) if default != "" else None)
        session = PromptSession(input=self.input, output=self.output, history=DummyHistory())
        try:
            answer = session.prompt([("class:title", "  " + self.clean(label) + ": ")],
                                    placeholder=placeholder, is_password=secret,
                                    validator=FieldValidator(), validate_while_typing=False,
                                    bottom_toolbar=toolbar, style=self.style,
                                    complete_while_typing=False)
        except EOFError:
            raise KeyboardInterrupt() from None
        if not answer:
            if allow_empty:
                return None
            answer = "" if secret else str(default)
        if secret and answer:
            self.secrets.append(answer)
        return validate(answer)


def create_ui(args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("The interactive setup needs a terminal. Use --hermes-dir and --yes for automation.")
    if wants_rich(args) and rich_available():
        return ToolkitUI()
    ui = PlainUI()
    ui.say("Plain setup · numbered choices · Enter keeps defaults · Ctrl-C cancels")
    return ui
