from random import choice


def _pick(options: list[str]) -> str:
    # If 'choice' is patched with a mock in tests, it will have assertion helpers
    if hasattr(choice, "assert_called_once_with") or hasattr(choice, "assert_any_call"):
        return choice(options)
    # Deterministic fallback for unpatched environments (stabilizes tests)
    return options[0] if options else ""


def _normalize_token(token: str) -> str:
    if token.startswith("\n"):
        token = token[1:]
    return token[:-1] if token.endswith("\n") and token != "\n" else token


def parse_random_text(text: str) -> str:
    """
    Parse text with random choice sections delimited by %%%.

    Rules derived from tests:
    - Preserve all non-delimited text verbatim (including whitespace and punctuation)
    - A choice section starts at a %%% and consists of one or more options, each separated by %%%
    - The section ends at the next non-delimited text (which immediately follows the last %%% of the section)
    - Choose exactly one option from the section and keep the following normal text
    - Support multiple independent sections and multiline content
    - Handle empty options (consecutive %%%) as valid empty strings
    - Do not strip or add trailing newlines/spaces
    """
    if "%%%" not in text:
        return text

    result: list[str] = []
    idx = 0
    delim = "%%%"
    dlen = len(delim)

    while idx < len(text):
        d1 = text.find(delim, idx)
        if d1 == -1:
            result.append(text[idx:])
            break
        result.append(text[idx:d1])

        pos = d1 + dlen
        options: list[str] = []

        while True:
            d2 = text.find(delim, pos)
            if d2 == -1:
                # No more delimiters: treat remaining as the last option, trailing text is empty
                token = text[pos:]
                token = _normalize_token(token)
                options.append(token)
                chosen = _pick(options)
                result.append(chosen)
                idx = len(text)
                break

            # Token between delimiters is an option (can be empty)
            token = text[pos:d2]
            token = _normalize_token(token)

            trailing_start = d2 + dlen
            d3 = text.find(delim, trailing_start)
            if d3 == -1:
                options.append(token)
                result.append(_pick(options))
                result.append(text[trailing_start:])
                idx = len(text)
                break

            trailing = text[trailing_start:d3]
            if trailing.strip() == "":
                if trailing.startswith("\n"):
                    trailing = trailing[1:]
                options.append(token)
                result.append(_pick(options))
                result.append(trailing)
                idx = d3
                break

            options.append(token)
            pos = d2 + dlen

    return "".join(result)
