"""Image origin labels are facts about message parts, never assistant prose."""


def kind(part, role):
    if part.get('source_message_id') is not None:
        return 'displayed_existing' if role == 'assistant' else 'quoted_image'
    return 'generated_result' if role == 'assistant' else 'user_upload'
