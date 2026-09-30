"""Hugging Face models: new models that are trending, with their size.

Only models created inside the window count, so a model that has been
trending for a month shows up once. Third-party derivatives (someone else's
fine-tune, quantization or LoRA of a base model) and format re-uploads (GGUF,
MLX, 4-bit...) are skipped by default. Parameter counts come from the
safetensors metadata and feed the device check.
"""
from __future__ import annotations

import re

from airec import device
from airec.sources.base import Item, Source, iso_epoch

API = "https://huggingface.co/api/models"
# Re-uploads in another format are not new models.
REPACK = re.compile(r"(gguf|gptq|awq|exl2|mlx|[-_](4|8)bit|[-_]fp8|[-_]nvfp4|lora)\b", re.I)
EXPAND = ["safetensors", "createdAt", "likes", "pipeline_tag", "downloads", "trendingScore",
          "cardData", "author"]


class HfModels(Source):
    name = "hf_models"
    track = "news"

    def fetch(self, since: float, until: float) -> list[Item]:
        limit = int(self.conf.get("limit", 200))
        min_likes = int(self.conf.get("min_likes", 20))
        skip_tags = set(self.conf.get("skip_pipeline_tags") or [])
        skip_derivatives = bool(self.conf.get("skip_derivatives", True))
        params = [("sort", "trendingScore"), ("direction", "-1"), ("limit", str(limit))]
        params += [("expand[]", e) for e in EXPAND]
        try:
            resp = self.http.get(API, params=params)
            resp.raise_for_status()
            models = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.warn(str(exc))
            return []
        items: list[Item] = []
        for m in models:
            created = iso_epoch(m.get("createdAt"))
            if not created or not since <= created <= until:
                continue
            if int(m.get("likes") or 0) < min_likes or m.get("pipeline_tag") in skip_tags:
                continue
            mid = m.get("id", "")
            card = m.get("cardData") or {}
            base = card.get("base_model")
            base = base[0] if isinstance(base, list) and base else base
            owner = mid.split("/")[0].lower()
            if skip_derivatives and REPACK.search(mid):
                continue
            if skip_derivatives and isinstance(base, str) and "/" in base \
                    and base.split("/")[0].lower() != owner:
                continue  # someone else's fine-tune, quantization or LoRA
            params_total = (m.get("safetensors") or {}).get("total")
            desc = [f"{m.get('pipeline_tag') or 'model'} by {m.get('author') or mid.split('/')[0]}"]
            if params_total:
                desc.append(f"{device.size_label(params_total)} parameters")
            if base:
                desc.append(f"based on {base}")
            if card.get("license"):
                desc.append(f"license {card['license']}")
            items.append({
                "id": f"hf:{mid.lower()}",
                "kind": "model",
                "source": self.name,
                "url": f"https://huggingface.co/{mid}",
                "title": mid,
                "abstract": ", ".join(desc),
                "authors": m.get("author") or mid.split("/")[0],
                "published_at": created,
                "signals": {
                    "likes": m.get("likes"),
                    "downloads": m.get("downloads"),
                    "trending": m.get("trendingScore"),
                    "task": m.get("pipeline_tag") or "",
                    "params": params_total or 0,
                    "device_fit": device.fit(params_total, self.ctx.cfg.settings),
                },
            })
        self.log(f"  hf_models: {len(items)} new trending models")
        return items
