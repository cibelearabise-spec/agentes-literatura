#!/usr/bin/env python3
"""
arxiv_plasma_watch.py
-----------------------
Agente de escopo fechado para o Fable 6 (propulsão a plasma): vigia o
arXiv por preprints novos sobre propulsores Hall, propulsão elétrica e
propulsão a plasma ionosférica, e registra só o que é NOVO desde a
última execução. Não decide nada, não roda simulação nenhuma — só
encontra e loga, pra alimentar a base teórica do projeto.

Por que arXiv e não PubMed: essa é literatura de física de plasma e
engenharia aeroespacial, não biomédica. O arXiv também é público e não
exige chave de API.

Uso:
  python3 arxiv_plasma_watch.py

Agendamento: mesmas opções dos outros dois agentes (cron semanal,
Agendador de Tarefas do Windows, ou GitHub Actions com commit do log).

Observação: este agente cobre a etapa "propulsor Hall ionosférico" da
arquitetura do Fable 6. A parte "ventoinha dutada / veículo esférico"
é mais aerodinâmica/estrutural do que plasma — se fizer sentido, dá pra
criar um quarto agente específico pra essa frente (ex.: vigiando
literatura de ducted fan / lighter-than-air / veículos esféricos).
"""

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------------------

# Sintaxe de busca do arXiv (campo ti = título, abs = resumo).
QUERY = (
    'abs:"Hall thruster" OR abs:"ion thruster" OR abs:"electric propulsion" '
    'OR abs:"plasma propulsion" OR abs:"ionospheric propulsion" '
    'OR abs:"air-breathing thruster"'
)

LOOKBACK_DAYS = 8
MAX_RESULTS = 50

BASE = "http://export.arxiv.org/api/query"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state_plasma.json")
LOG_FILE = os.path.join(HERE, "log_plasma.md")

ATOM_NS = "{http://www.w3.org/2005/Atom}"


# ---------------------------------------------------------------------------
# FUNÇÕES
# ---------------------------------------------------------------------------

def _get(url, params):
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{qs}", timeout=30) as resp:
        return resp.read()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def search_arxiv():
    """Busca no arXiv, ordenado por data de submissão (mais recente primeiro)."""
    import xml.etree.ElementTree as ET

    params = {
        "search_query": QUERY,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": MAX_RESULTS,
    }
    raw = _get(BASE, params)
    root = ET.fromstring(raw)

    entries = []
    cutoff = datetime.now(timezone.utc).timestamp() - LOOKBACK_DAYS * 86400

    for entry in root.findall(f"{ATOM_NS}entry"):
        arxiv_id_full = entry.find(f"{ATOM_NS}id").text.strip()
        arxiv_id = re.sub(r"^https?://arxiv\.org/abs/", "", arxiv_id_full)

        published_el = entry.find(f"{ATOM_NS}published")
        published = published_el.text.strip() if published_el is not None else ""
        try:
            pub_ts = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            pub_ts = 0

        if pub_ts < cutoff:
            continue  # fora da janela de lookback, ignora

        title = entry.find(f"{ATOM_NS}title").text.strip().replace("\n", " ")
        summary = entry.find(f"{ATOM_NS}summary").text.strip().replace("\n", " ")
        authors = [
            a.find(f"{ATOM_NS}name").text
            for a in entry.findall(f"{ATOM_NS}author")
        ]
        authors_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        entries.append({
            "id": arxiv_id,
            "title": title,
            "authors": authors_str,
            "published": published[:10],
            "summary": summary[:280] + ("…" if len(summary) > 280 else ""),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return entries


def append_log(new_entries):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## Rodada — {ts}\n"]
    if not new_entries:
        lines.append("Nenhum preprint novo desde a última execução.\n")
    else:
        lines.append(f"{len(new_entries)} preprint(s) novo(s):\n")
        for e in new_entries:
            lines.append(
                f"- **{e['title']}**\n"
                f"  {e['authors']} — arXiv, {e['published']}\n"
                f"  {e['url']}\n"
                f"  _{e['summary']}_\n"
            )
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    state = load_state()
    seen = set(state.get("seen_ids", []))

    entries = search_arxiv()
    new_entries = [e for e in entries if e["id"] not in seen]

    append_log(new_entries)

    seen.update(e["id"] for e in entries)
    state["seen_ids"] = sorted(seen)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"Rodada concluída: {len(new_entries)} preprint(s) novo(s) registrados em {LOG_FILE}")


if __name__ == "__main__":
    main()
