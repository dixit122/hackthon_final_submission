from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import PPOConfig, PPOTrainer, AutoModelForCausalLMWithValueHead

from client import JiraOutlookEnv
from models import JiraOutlookAction

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / 'data' / 'tasks' / 'robust_episodes'
SYSTEM_PROMPT = (
    'You are a careful ticket triage agent working in a constrained tool-use environment. '
    'Use only the provided Jira and Outlook tools. Return exactly one JSON action.'
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-model', default='Qwen/Qwen3-4B-Instruct-2507')
    parser.add_argument('--sft-adapter', default='training/output/qwen3_4b_sft_v2')
    parser.add_argument('--output-dir', default='training/output/qwen3_4b_ppo')
    parser.add_argument('--env-base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--steps', type=int, default=6)
    parser.add_argument('--episodes', type=int, default=6)
    return parser.parse_args()


def load_policy(base_model: str, adapter_path: str):
    model = AutoModelForCausalLM.from_pretrained(base_model, torch_dtype='auto', device_map='auto')
    model = PeftModel.from_pretrained(model, adapter_path)
    value_model = AutoModelForCausalLMWithValueHead.from_pretrained(model)
    tokenizer = AutoTokenizer.from_pretrained(adapter_path, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return value_model, tokenizer


def build_prompt(observation: dict) -> str:
    payload = {
        'instructions': [
            'Return exactly one JSON action.',
            'Use only get_jira_ticket, search_jira, get_outlook_mail, search_outlook, submit_resolution.',
            'If submitting duplicate, include the canonical Jira id in resolution_notes.',
        ],
        'observation': observation,
    }
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{json.dumps(payload, indent=2)}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


def parse_action(text: str) -> JiraOutlookAction:
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        raise ValueError(f'No JSON action in: {text}')
    return JiraOutlookAction(**json.loads(text[start:end + 1]))


async def rollout_episode(env: JiraOutlookEnv, tokenizer, model, task_id: str, max_steps: int):
    reset = await env.reset(task_id=task_id)
    observation = reset.observation
    prompt_texts = []
    response_texts = []
    rewards = []

    for _ in range(max_steps):
        obs_payload = {
            'task': observation.task.model_dump(mode='json') if observation.task else None,
            'assigned_ticket': observation.assigned_ticket.model_dump(mode='json') if observation.assigned_ticket else None,
            'returned_record': observation.returned_record,
            'jira_results': [hit.model_dump(mode='json') for hit in observation.jira_results],
            'outlook_results': [hit.model_dump(mode='json') for hit in observation.outlook_results],
            'reward': observation.reward,
            'done': observation.done,
            'last_action_error': observation.last_action_error,
            'steps_taken': observation.steps_taken,
        }
        prompt = build_prompt(obs_payload)
        inputs = tokenizer(prompt, return_tensors='pt').to(model.pretrained_model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=96, do_sample=True, top_p=0.9, temperature=0.7)
        response = tokenizer.decode(out[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True)
        try:
            action = parse_action(response)
        except Exception:
            action = JiraOutlookAction(tool='search_jira', query='error', fields=['ticket_number'])
        step = await env.step(action)
        observation = step.observation
        if step.reward is not None:
            observation.reward = step.reward
        if step.done:
            observation.done = True

        prompt_texts.append(prompt)
        response_texts.append(response)
        rewards.append(float(observation.reward))
        if observation.done:
            break

    return prompt_texts, response_texts, rewards


async def main_async():
    args = parse_args()
    model, tokenizer = load_policy(args.base_model, args.sft_adapter)
    ppo_config = PPOConfig(
        batch_size=4,
        mini_batch_size=1,
        learning_rate=1e-5,
        log_with=None,
    )
    trainer = PPOTrainer(config=ppo_config, model=model, ref_model=None, tokenizer=tokenizer)

    task_ids = sorted(p.stem for p in EPISODES_DIR.glob('*.json'))[: args.episodes]
    env = JiraOutlookEnv(base_url=args.env_base_url)
    all_rewards = []

    for task_id in task_ids:
        prompts, responses, rewards = await rollout_episode(env, tokenizer, model, task_id, args.steps)
        if prompts:
            all_rewards.extend(rewards)
            for start in range(0, len(prompts), 4):
                batch_prompts = prompts[start:start + 4]
                batch_responses = responses[start:start + 4]
                batch_rewards = rewards[start:start + 4]
                if not batch_prompts:
                    continue
                query_tensors = [tokenizer(p, return_tensors='pt')['input_ids'][0].to(model.pretrained_model.device) for p in batch_prompts]
                response_tensors = [tokenizer(r, return_tensors='pt')['input_ids'][0].to(model.pretrained_model.device) for r in batch_responses]
                reward_tensors = [torch.tensor(r, device=model.pretrained_model.device) for r in batch_rewards]
                trainer.config.batch_size = len(query_tensors)
                trainer.step(query_tensors, response_tensors, reward_tensors)

    await env.close()
    trainer.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(json.dumps({
        'episodes': len(task_ids),
        'mean_reward': sum(all_rewards) / len(all_rewards) if all_rewards else 0.0,
        'num_rewards': len(all_rewards),
        'output_dir': args.output_dir,
    }, indent=2))


def main():
    asyncio.run(main_async())


if __name__ == '__main__':
    main()
