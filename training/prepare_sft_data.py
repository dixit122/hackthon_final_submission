from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / 'data' / 'tasks' / 'robust_episodes'
GROUND_TRUTH = json.loads((ROOT / 'data' / 'jira_outlook_robust_case.json').read_text())
OUT_PATH = ROOT / 'data' / 'training' / 'sft_train.jsonl'

SYSTEM_PROMPT = (
    'You are a careful ticket triage agent working in a constrained tool-use environment. '
    'Use only the provided Jira and Outlook tools. Return exactly one JSON action.'
)

GT_BY_ID = {r['ticket_number']: r for r in GROUND_TRUTH['jira_records']}

KEY_PHRASES = [
    'IllegalStateException',
    'NotificationPreferenceAssembler',
    'empty locale map',
    'profile hydration',
    'duplicate redemption',
    'ledger_event_id',
    'certificate thumbprint',
    'invoice footer',
    'template revision',
    'sample output',
    'acknowledgement template',
    'renderer fallback',
    'locale fallback',
    'tax rounding mismatch',
    'currency switch',
    'digest notifications',
    'timezone migration',
]


def build_examples_for_episode(task: dict) -> list[dict]:
    ticket = task['assigned_ticket_number']
    gt = GT_BY_ID[ticket]
    examples = []
    assigned = next(r for r in task['jira_records'] if r['ticket_number'] == ticket)

    obs0 = {
        'task_id': task['task_id'],
        'assigned_ticket_number': ticket,
        'objective': task['objective'],
        'steps_taken': 0,
        'assigned_ticket': assigned,
        'history': [],
    }

    path = gt.get('ground_truth_path', [])
    resolution = gt.get('resolution')
    resolution_notes = gt.get('resolution_notes')

    search_queries = _query_variants_from_logs(gt['logs'])

    if resolution == 'duplicate' and path:
        canonical = path[-1]
        bridge_nodes = [node for node in path[1:-1] if node.startswith('MAIL-') or node.startswith('JIRA-')]

        for query in search_queries[:2]:
            examples.append(_message_example(obs0, {'tool': 'search_jira', 'query': query}))

        history = [
            {'action': {'tool': 'search_jira', 'query': search_queries[0]}, 'result_hint': f'Finds canonical or bridge evidence for {ticket}'}
        ]
        obs1 = {**obs0, 'steps_taken': 1, 'history': history}
        examples.append(_message_example(obs1, {'tool': 'get_jira_ticket', 'ticket_number': canonical}))

        if bridge_nodes:
            first_bridge = bridge_nodes[0]
            if first_bridge.startswith('MAIL-'):
                examples.append(_message_example(obs1, {'tool': 'get_outlook_mail', 'mail_id': first_bridge}))
            else:
                examples.append(_message_example(obs1, {'tool': 'get_jira_ticket', 'ticket_number': first_bridge}))

        history2 = history + [
            {'action': {'tool': 'get_jira_ticket', 'ticket_number': canonical}, 'result_hint': f'Reveals closed canonical Jira {canonical}'}
        ]
        obs2 = {**obs0, 'steps_taken': 2, 'history': history2}
        examples.append(
            _message_example(
                obs2,
                {
                    'tool': 'submit_resolution',
                    'ticket_number': ticket,
                    'resolution': 'duplicate',
                    'resolution_notes': resolution_notes,
                },
            )
        )
    elif resolution == 'needs_more_info':
        for query in search_queries[:2]:
            examples.append(_message_example(obs0, {'tool': 'search_outlook', 'query': query}))
        history = [
            {'action': {'tool': 'search_outlook', 'query': search_queries[0]}, 'result_hint': f'Finds missing-information thread for {ticket}'}
        ]
        obs1 = {**obs0, 'steps_taken': 1, 'history': history}
        examples.append(_message_example(obs1, {'tool': 'search_jira', 'query': search_queries[0]}))
        obs2 = {**obs0, 'steps_taken': 2, 'history': history + [{'action': {'tool': 'search_jira', 'query': search_queries[0]}, 'result_hint': 'Confirms no strong closed duplicate evidence'}]}
        examples.append(
            _message_example(
                obs2,
                {
                    'tool': 'submit_resolution',
                    'ticket_number': ticket,
                    'resolution': 'needs_more_info',
                },
            )
        )
    else:
        for query in search_queries[:3]:
            examples.append(_message_example(obs0, {'tool': 'search_jira', 'query': query}))

    return examples


def _query_variants_from_logs(logs: str) -> list[str]:
    matched = [phrase for phrase in KEY_PHRASES if phrase.lower() in logs.lower()]
    variants = []
    if matched:
        variants.append(' '.join(matched[:4]))
        variants.append(' '.join(matched[:2]))
    words = logs.split()
    variants.append(' '.join(words[:12]))
    variants.append(' '.join(words[12:24]))
    deduped = []
    for item in variants:
        item = ' '.join(item.split())
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _message_example(obs: dict, action: dict) -> dict:
    user_content = json.dumps(obs, indent=2)
    assistant_content = json.dumps(action, ensure_ascii=False)
    return {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
            {'role': 'assistant', 'content': assistant_content},
        ]
    }


def main() -> None:
    examples = []
    for path in sorted(EPISODES_DIR.glob('*.json')):
        task = json.loads(path.read_text())
        examples.extend(build_examples_for_episode(task))
    OUT_PATH.write_text(''.join(json.dumps(item) + '\n' for item in examples))
    print(f'wrote {len(examples)} examples to {OUT_PATH}')


if __name__ == '__main__':
    main()
