"""Build model-facing save calls; domain fixtures keep native ItemUpdate records."""
import copy
from tests.test_task_execution_model_bridge import call as native_call


def model_arguments(value):
    result = copy.deepcopy(value)
    if isinstance(result, dict):
        for update in result.get('updates', ()):
            if isinstance(update.get('item'), dict):
                fields = update.pop('item')
                update.update(fields)
    return result


def call(name='update_task_plan', arguments=None, key='control'):
    return native_call(name, model_arguments(arguments) if name == 'save_task_text' else arguments, key)
