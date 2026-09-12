# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
r"""人格 LoRA 训练（P3 正式脚本）。

背景：llamafactory-cli 入口在 Windows 下有静默退出 bug（模型加载后无输出退出），
因此直接调用 llamafactory 内部流程（与 CLI 同路径，已验证可完整训练）。

产物：
- E:/robot/data/models/lora/persona_adapter/  标准 LoRA adapter（peft 格式，可被 llama-server --lora / transformers 加载）
- 训练日志 data/train_persona.log

用法: .venv-train\Scripts\python.exe -u tools\train_persona.py [--dataset seed_train]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = r"E:/robot/data/models/lora/persona"
ADAPTER_DIR = r"E:/robot/data/models/lora/persona_adapter"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="seed_train", help="seed_train 或 train（真实对话）")
    ap.add_argument("--epochs", type=int, default=3)
    args = ap.parse_args()

    import shutil

    import torch

    from llamafactory.data import SFTDataCollatorWith4DAttentionMask, get_dataset, get_template_and_fix_tokenizer
    from llamafactory.extras.constants import IGNORE_INDEX
    from llamafactory.hparams import get_train_args, read_args
    from llamafactory.model import load_model, load_tokenizer
    from llamafactory.train.sft.trainer import CustomSeq2SeqTrainer

    # 切换数据集与轮数（直接改 yaml 对应行）
    yaml_path = ROOT / "configs" / "lora_persona.yaml"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    import re

    yaml_text = re.sub(r"dataset: .*", f"dataset: {args.dataset}", yaml_text)
    yaml_text = re.sub(r"num_train_epochs: .*", f"num_train_epochs: {args.epochs}", yaml_text)
    yaml_path.write_text(yaml_text, encoding="utf-8")

    sys.argv = ["train", str(yaml_path)]
    model_args, data_args, training_args, finetuning_args, generating_args = get_train_args(read_args(None))

    tok_module = load_tokenizer(model_args)
    template = get_template_and_fix_tokenizer(tok_module["tokenizer"], data_args)
    dataset_module = get_dataset(template, model_args, data_args, training_args, stage="sft", **tok_module)
    print(f"[train] dataset={args.dataset} samples={len(dataset_module['train_dataset'])}", flush=True)

    model = load_model(tok_module["tokenizer"], model_args, finetuning_args, training_args.do_train)
    print("[train] model loaded, trainable:", sum(p.numel() for p in model.parameters() if p.requires_grad), flush=True)

    collator = SFTDataCollatorWith4DAttentionMask(
        template=template,
        model=model,
        pad_to_multiple_of=8,
        label_pad_token_id=IGNORE_INDEX,
        block_diag_attn=model_args.block_diag_attn,
        neat_packing=data_args.neat_packing,
        attn_implementation=getattr(model.config, "_attn_implementation", None),
        compute_dtype=model_args.compute_dtype,
        **tok_module,
    )
    trainer = CustomSeq2SeqTrainer(
        model=model,
        args=training_args,
        finetuning_args=finetuning_args,
        data_collator=collator,
        callbacks=[],
        gen_kwargs={},
        **dataset_module,
        **tok_module,
    )

    print("[train] training start", flush=True)
    result = trainer.train()
    print("[train] done:", result.metrics.get("train_loss"), flush=True)

    # 提取标准 LoRA adapter（peft 格式），删除 llamafactory 保存的全量权重
    if Path(ADAPTER_DIR).exists():
        shutil.rmtree(ADAPTER_DIR)
    trainer.model.save_pretrained(ADAPTER_DIR)
    print(f"[train] adapter saved -> {ADAPTER_DIR}", flush=True)
    for p in [Path(OUT_DIR), Path(OUT_DIR) / "checkpoint-15"]:
        full = p / "model.safetensors"
        if full.exists():
            full.unlink()
            print(f"[train] removed full weights: {full}", flush=True)

    torch.cuda.empty_cache()
    print("[train] all done", flush=True)


if __name__ == "__main__":
    main()
