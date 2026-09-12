"""atomics —— 状态文件原子读写（2026-09-06）。

审计教训（P2）：历史实现直接 `write_text`，进程中途被杀/磁盘满会留下半截 JSON；
而读侧捕获 JSONDecodeError 返回空 —— 一次写坏 = 全部用户状态静默清零
（催眠 orig 丢失 → 亲密度永久停在临时值）。

约定：
    写：同目录临时文件 + os.replace（NTFS 同卷原子替换）
    读：utf-8-sig（防 BOM，项目铁律）；损坏返回 default 且留 warning 日志
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from loguru import logger


def write_text_atomic(path: Path | str, text: str) -> bool:
    """原子写文本（utf-8）。失败返回 False（调用方决定降级；不抛出）。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                # B14（2026-09-09 审计）：replace 前强制落盘。NTFS 回写缓存下 os.replace 的名字原子性
                # 不保证新内容已写盘——进程中途被杀/断电时可能换上一个空/半截 tmp（状态静默清零同款事故面）
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(p))
            return True
        except Exception:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        logger.warning("atomic write failed: {} [{}]", p, type(e).__name__)
        return False


def write_bytes_atomic(path: Path | str, data: bytes) -> bool:
    """原子写字节（2026-09-12 S1 审计补：harness 恢复状态文件时用的是 write_bytes）。

    与 write_text_atomic 同款纪律（同目录 tmp + fsync + os.replace）。存在的理由：
    harness.StateFixture 把真实状态文件**按原字节**备份/恢复，走文本层会引入
    编解码往返（BOM、非法字节），一旦有损就是"恢复了但内容变了"——比不恢复更坏。
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(p))
            return True
        except Exception:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        logger.warning("atomic byte write failed: {} [{}]", p, type(e).__name__)
        return False


def write_json_atomic(path: Path | str, obj) -> bool:
    """原子写 JSON（ensure_ascii=False, indent=2，utf-8 无 BOM）。"""
    return write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: Path | str, default):
    """读 JSON（utf-8-sig 防 BOM）。缺失/损坏返回 default；损坏留日志（不再静默吞）。"""
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("state file unreadable (using default): {} [{}]", p, type(e).__name__)
        return default


def quarantine_corrupt(path: Path | str) -> bool:
    """损坏状态文件隔离（C10，对齐 wishes fail-closed 模式）：改名 <原名>.corrupt 后返回 True。
    语义=「拒绝覆盖」：坏文件不删除（留取证/手工恢复），也不留在原位被后续
    「读到默认 → 原子写回」的路径覆盖清掉。改名失败（占用/权限）返回 False。"""
    p = Path(path)
    try:
        if p.exists():
            p.replace(p.parent / (p.name + ".corrupt"))
            logger.warning("corrupt state file quarantined: {} -> {}.corrupt", p, p.name)
        return True
    except OSError as e:
        logger.warning("quarantine rename failed: {} [{}]", p, type(e).__name__)
        return False
