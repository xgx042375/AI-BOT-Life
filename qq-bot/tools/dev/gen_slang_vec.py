# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
import json, sys, time, urllib.request
sys.path.insert(0, r"E:\robot\qq-bot")
# 合并三词库（net_slang / slang_anime / slang_game）→ 单表
FILES = [
    r"E:\robot\data\net_slang.json",
    r"E:\robot\data\slang_anime.json",
    r"E:\robot\data\slang_game.json",
]
slang = {}
for f in FILES:
    slang.update(json.load(open(f, encoding="utf-8")))
terms = list(slang)
out = {}

def embed_batch(texts):
    req = urllib.request.Request(
        "http://127.0.0.1:11435/v1/embeddings",
        data=json.dumps({"model": "qwen3-embedding", "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [d["embedding"] for d in r["data"]]

t0 = time.time()
for i in range(0, len(terms), 32):
    batch = terms[i:i + 32]
    vecs = embed_batch([slang[t][:60] for t in batch])
    for t, v in zip(batch, vecs):
        out[t] = v
    print(f"batch {i}-{i+len(batch)} ok ({time.time()-t0:.0f}s)", flush=True)
json.dump(out, open(r"E:\robot\data\slang_semantic.json", "w", encoding="utf-8"))
print(f"DONE {len(out)}/{len(terms)} in {time.time()-t0:.0f}s")
