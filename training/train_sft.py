from __future__ import annotations

import argparse
from pathlib import Path

from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from trl import SFTTrainer


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-name', default='Qwen/Qwen3-4B-Instruct-2507')
    parser.add_argument('--train-file', default='data/training/sft_train.jsonl')
    parser.add_argument('--output-dir', default='training/output/qwen3_4b_sft')
    parser.add_argument('--num-train-epochs', type=float, default=3.0)
    parser.add_argument('--per-device-train-batch-size', type=int, default=2)
    parser.add_argument('--gradient-accumulation-steps', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--max-seq-length', type=int, default=2048)
    parser.add_argument('--logging-steps', type=int, default=5)
    parser.add_argument('--save-steps', type=int, default=50)
    return parser.parse_args()


def format_example(example):
    parts = []
    for msg in example['messages']:
        parts.append(f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>")
    return {'text': '\n'.join(parts)}


def main():
    args = parse_args()
    dataset = load_dataset('json', data_files=args.train_file, split='train')
    dataset = dataset.map(format_example)

    tokenizer = AutoTokenizer.from_pretrained(args.model_name, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        torch_dtype='auto',
        device_map='auto',
    )

    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias='none',
        task_type='CAUSAL_LM',
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'up_proj', 'down_proj', 'gate_proj'],
    )

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.num_train_epochs,
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        bf16=True,
        report_to='none',
        remove_unused_columns=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
        formatting_func=lambda ex: ex['text'],
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)


if __name__ == '__main__':
    main()
