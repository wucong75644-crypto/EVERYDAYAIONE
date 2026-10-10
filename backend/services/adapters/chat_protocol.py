"""Normalize multimodal planner messages at the Chat Completions boundary."""
from copy import deepcopy


def chat_messages(messages):
    result = deepcopy(messages)
    for message in result:
        if message.get("role") == "developer":
            message["role"] = "system"
        if not isinstance(message.get("content"), list):
            continue
        for part in message["content"]:
            if part.get("type") == "input_text":
                part["type"] = "text"
            elif part.get("type") == "input_image":
                part["type"] = "image_url"
                url = part.pop("image_url", None)
                part["image_url"] = {"url": url} if isinstance(url, str) else url
    return result
