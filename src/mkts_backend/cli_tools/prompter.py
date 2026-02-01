from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML


def prompt_continuation(width, line_number, wrap_count):
    """
    The continuation: display line numbers and '->' before soft wraps.

    Notice that we can return any kind of formatted text from here.

    The prompt continuation doesn't have to be the same width as the prompt
    which is displayed before the first line, but in this example we choose to
    align them. The `width` input that we receive here represents the width of
    the prompt.
    """
    if wrap_count > 0:
        return " " * (width - 3) + "-> "
    else:
        text = ("- %i - " % (line_number + 1)).rjust(width)
        return HTML("<strong>%s</strong>") % text


def get_multiline_input() -> str:
    print("Press [Meta+Enter] or [Esc] followed by [Enter] to accept input.")
    fit = prompt(
        "Multiline input: ", multiline=True, prompt_continuation=prompt_continuation
    )
    print("--------------------------------------")
    print(f"you entered: {fit}")
    return fit


if __name__ == "__main__":
    pass
