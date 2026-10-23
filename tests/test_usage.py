"""Token accounting: per role, per answer, and correct when answers run at the same time."""

import asyncio

from langchain_core.messages import AIMessage

from marginalia.agent.usage import TokenUsage, UsageCallback
from tests.test_agent import ScriptedModel, agent_for, script, server, tool_call  # noqa: F401  (fixture)


def reply(text, prompt, completion, seconds):
    return AIMessage(text, usage_metadata={'input_tokens': prompt, 'output_tokens': completion,
                                           'total_tokens': prompt + completion},
                     response_metadata={'eval_count': completion, 'eval_duration': int(seconds * 1e9)})


def test_a_usage_sums_calls_by_role():
    usage = TokenUsage()
    usage.add('agent', reply('a', 100, 40, 2.0))
    usage.add('agent', reply('b', 300, 60, 3.0))
    usage.add('grader', reply('c', 500, 20, 1.0))
    s = usage.summary()
    assert s['agent'] == {'calls': 2, 'prompt': 400, 'completion': 100, 'total': 500, 'seconds': 5.0, 'per_second': 20.0}
    assert s['grader']['total'] == 520
    assert s['total'] == {'calls': 3, 'prompt': 900, 'completion': 120, 'total': 1020, 'seconds': 6.0, 'per_second': 20.0}


def test_metadata_from_ollama_alone_is_enough():
    usage = TokenUsage()
    usage.add('agent', AIMessage('x', response_metadata={'prompt_eval_count': 12, 'eval_count': 8, 'eval_duration': 0}))
    assert usage.summary()['agent'] == {'calls': 1, 'prompt': 12, 'completion': 8, 'total': 20, 'seconds': 0.0,
                                        'per_second': None}


def test_the_callback_counts_only_inside_counting():
    usage, callback = TokenUsage(), UsageCallback('agent')

    class Result:
        generations = [[type('G', (), {'message': reply('x', 10, 5, 1.0)})()]]

    callback.on_llm_end(Result())                      # nobody is counting
    with usage.counting():
        callback.on_llm_end(Result())
    assert usage.summary()['total']['calls'] == 1


def test_each_answer_counts_its_own_calls_even_when_answered_together(server):  # noqa: F811
    def respond(messages):
        question = next(m.content for m in messages if m.type == 'human')
        n = 10 if 'one' in question else 1000
        return reply('An answer without citations.', n, n // 10, 1.0)   # no revision: one call each

    async def run():
        async with agent_for(server, respond) as (agent, model):
            model.callbacks = [UsageCallback('agent')]
            return await asyncio.gather(agent.ask('question one'), agent.ask('question two'))
    one, two = asyncio.run(run())
    assert one.usage['agent']['prompt'] == 10 and one.usage['agent']['calls'] == 1
    assert two.usage['agent']['prompt'] == 1000
