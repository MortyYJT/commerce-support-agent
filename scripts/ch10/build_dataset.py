# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
"""主题分类数据集:分层划分 80/10/10,只增强训练集,保持验证集和测试集原样。
运行 make ch10-dataset 需要配置聊天模型上游。"""
import asyncio
import json
import pathlib

from app.core.llm import get_chat_model
from app.core.taxonomy import TOPIC_NAMES
from scripts.ch10.corpus_lib import split_dataset

SRC = pathlib.Path("data/ch10/corpus_labeled.jsonl")
OUT = pathlib.Path("data/ch10/dataset")
# 第一版评估翻车的定向补数:光杆「买大了想退」类短句(无商品名/无「尺码」字眼)
SUPPLEMENT = pathlib.Path(__file__).parent / "supplement_sizefit.jsonl"

AUGMENT_PROMPT = """把下面这句电商客服用户问题改写一个变体:换同义词、微调句式(比如改成「我想问一下……」的口气),
不改原意、不增删诉求。只输出改写后的句子。

{text}"""


async def augment(samples: list[dict], concurrency: int = 8) -> list[dict]:
    model = get_chat_model()
    sem = asyncio.Semaphore(concurrency)

    async def one(s: dict) -> dict | None:
        async with sem:
            try:
                r = await model.ainvoke(AUGMENT_PROMPT.format(text=s["text"]))
                t = r.content.strip()
            except Exception as e:
                print(f"[augment] 改写失败,丢弃该变体: {s['text'][:30]}… ({type(e).__name__})")
                return None
        return {"text": t, "labels": s["labels"], "origin": "augmented"} if t else None

    outs = await asyncio.gather(*[one(s) for s in samples])
    return [o for o in outs if o]


def _dump(path: pathlib.Path, samples: list[dict]) -> None:
    path.write_text("\n".join(
        json.dumps({"text": s["text"], "labels": s["labels"]}, ensure_ascii=False)
        for s in samples), encoding="utf-8")


def _dist(name: str, samples: list[dict]) -> None:
    counts = {n: 0 for n in TOPIC_NAMES}
    for s in samples:
        for lb in s["labels"]:
            counts[lb] += 1
    print(f"{name}({len(samples)} 条): " + " ".join(f"{k}={v}" for k, v in counts.items()))


async def main() -> None:
    samples = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    train, val, test = split_dataset(samples)
    aug = await augment(train)
    seen = {s["text"] for s in samples}          
    train = train + [a for a in aug if a["text"] not in seen]
    if SUPPLEMENT.exists():                       # 定向补数只进训练集
        sup = [json.loads(l) for l in SUPPLEMENT.read_text(encoding="utf-8").splitlines()
               if l.strip()]
        train = train + [{**s, "origin": "supplement"} for s in sup
                         if s["text"] not in seen]
    OUT.mkdir(parents=True, exist_ok=True)
    for name, ds in (("train", train), ("val", val), ("test", test)):
        _dump(OUT / f"{name}.jsonl", ds)
        _dist(name, ds)


if __name__ == "__main__":
    asyncio.run(main())
