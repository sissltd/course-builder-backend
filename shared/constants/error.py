# The `print(response.data)` statement is used to output the `data` attribute of the `response`
# object to the console. This is helpful for debugging and understanding the structure of the data
# being returned in the response object at that point in the code execution. It allows you to see
# the content of the response data and helps in identifying any issues or understanding the data
# flow within the custom exception handling logic.
def format_errors(errors):
    """
    Converts DRF errors into:
    { field: [messages] }
    """

    normalized = {}

    for k, v in errors.items():
        if isinstance(v, list):
            if isinstance(v[0], dict):  # Validation error raised by a nested serializer
                x = list(v[0].items())[0]
                y = f"{x[0]}: {x[1][0]!s}"
                if x[0].lower() == "non_field_errors":
                    y = str(x[1][0])
                normalized[k] = [y]
            else:
                normalized[k] = [str(e) for e in v]
        else:
            # handles {'detail': ErrorDetail(...)}
            normalized[k] = [str(v)]

    return normalized


def extract_first_error(errors):
    errors = format_errors(errors)

    first_field = next(iter(errors))
    first_message = errors[first_field][0]

    # For DRF "detail" errors, return directly
    if first_field == "detail":
        return first_message

    message = str(first_message).replace("This field", first_field)

    return f"{first_field} {message.lower().replace(first_field.lower(), '').strip()}"


# def format_errors(errors):
#     # make ErrorDetail objects into plain strings, and keep lists
#     return {k: [str(e) for e in v] for k, v in errors.items()}


# def extract_first_error(errors):
#     # errors = {"email": ["This field is required."]}

#     first_field = next(iter(errors))
#     first_message = errors[first_field][0]

#     # Normalize DRF default wording
#     message = str(first_message).replace("This field", first_field)

#     return f"{first_field} {message.lower().replace(first_field.lower(), '').strip()}"


def extract_response_error(response):
    try:
        data = response.json()
        if isinstance(data, list):
            return "; ".join(data)
        if isinstance(data, dict):
            return data.get("message") or str(data)
        return str(data)
    except ValueError:
        return response.text
