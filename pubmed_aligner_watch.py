#!/usr/bin/env python3
"""
pubmed_aligner_watch.py
------------------------
Agente de escopo fechado: vigia a PubMed por literatura nova sobre
biomecânica de alinhadores transparentes e registra só o que é NOVO
desde a última execução. Não decide nada sozinho — só encontra e loga.

Como funciona:
  1. Consulta a PubMed (E-utilities, API pública, sem chave necessária)
     com a query definida em QUERY.
  2. Compara os PMIDs retornados com o que já foi visto (state.json).
  3. Para os PMIDs novos, busca título/autores/revista/data/DOI.
  4. Acrescenta uma seção com data no log.md (nunca sobrescreve).
  5. Atualiza state.json com os PMIDs já vistos.

Uso:
  python3 pubmed_aligner_watch.py

Agendamento (escolha um):
  - cron (Linux/Mac), toda segunda às 8h:
      0 8 * * 1 /usr/bin/python3 /caminho/pubmed_aligner_watch.py
  - Agendador de Tarefas do Windows: rodar semanalmente apontando pro
    mesmo comando.
  - GitHub Actions: um workflow com "schedule: cron" semanal, que roda
    o script e faz commit do log.md e state.json atualizados no repo —
    assim o log fica versionado e visível de qualquer lugar.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO — ajuste aqui, não no corpo do script
# ---------------------------------------------------------------------------

# Query da PubMed (sintaxe padrão de busca).
# Cobre biomecânica de alinhadores em geral: movimento dentário, FEA,
# trimline, attachments, força/torque.
QUERY = (
    '("clear aligner"[tiab] OR "clear aligners"[tiab] OR "invisalign"[tiab]) '
    'AND (biomechanic*[tiab] OR "tooth movement"[tiab] OR "finite element"[tiab] '
    'OR "force"[tiab] OR "torque"[tiab] OR trimline[tiab] OR attachment*[tiab])'
)

# Só considera artigos publicados nos últimos N dias na hora de rodar
# (evita reprocessar o histórico inteiro toda vez).
LOOKBACK_DAYS = 8

# Máximo de resultados por rodada (a PubMed pode ter mais; isso é um teto
# de segurança, não um filtro de qualidade).
MAX_RESULTS = 60

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.md")

# Opcional: coloque seu e-mail aqui — a NCBI pede isso como boa prática
# para identificar quem está usando a API pública. Não é obrigatório.
CONTACT_EMAIL = ""


# ---------------------------------------------------------------------------
# FUNÇÕES
# ---------------------------------------------------------------------------

def _get(url, params):
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    with urllib.request.urlopen(full_url, timeout=30) as resp:
        return resp.read()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_pmids": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def search_pubmed():
    """Retorna lista de PMIDs que batem com a query, restrita ao lookback."""
    params = {
        "db": "pubmed",
        "term": QUERY,
        "retmax": MAX_RESULTS,
        "retmode": "json",
        "sort": "pub+date",
        "datetype": "pdat",
        "reldate": LOOKBACK_DAYS,
    }
    if CONTACT_EMAIL:
        params["email"] = CONTACT_EMAIL
    raw = _get(f"{BASE}/esearch.fcgi", params)
    data = json.loads(raw)
    return data.get("esearchresult", {}).get("idlist", [])


def fetch_summaries(pmids):
    """Busca metadados (título, autores, revista, data, DOI) para os PMIDs dados."""
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    if CONTACT_EMAIL:
        params["email"] = CONTACT_EMAIL
    raw = _get(f"{BASE}/efetch.fcgi", params)
    root = ET.fromstring(raw)

    articles = []
    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else "?"

        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else "(sem título)"

        journal_el = art.find(".//Journal/Title")
        journal = journal_el.text if journal_el is not None else ""

        year_el = art.find(".//JournalIssue/PubDate/Year")
        medline_date_el = art.find(".//JournalIssue/PubDate/MedlineDate")
        year = (
            year_el.text if year_el is not None
            else (medline_date_el.text if medline_date_el is not None else "")
        )

        authors = []
        for author in art.findall(".//AuthorList/Author"):
            last = author.find("LastName")
            initials = author.find("Initials")
            if last is not None:
                name = last.text
                if initials is not None:
                    name += f" {initials.text}"
                authors.append(name)
        authors_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        doi = ""
        for eid in art.findall(".//ELocationID"):
            if eid.get("EIdType") == "doi":
                doi = eid.text
        if not doi:
            for aid in art.findall(".//ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text

        articles.append({
            "pmid": pmid,
            "title": title,
            "journal": journal,
            "year": year,
            "authors": authors_str,
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return articles


def append_log(new_articles):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## Rodada — {ts}\n"]
    if not new_articles:
        lines.append("Nenhum artigo novo desde a última execução.\n")
    else:
        lines.append(f"{len(new_articles)} artigo(s) novo(s):\n")
        for a in new_articles:
            lines.append(
                f"- **{a['title']}**\n"
                f"  {a['authors']} — {a['journal']}, {a['year']}\n"
                f"  {a['url']}" + (f" · DOI: {a['doi']}" if a["doi"] else "") + "\n"
            )
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    state = load_state()
    seen = set(state.get("seen_pmids", []))

    pmids = search_pubmed()
    new_pmids = [p for p in pmids if p not in seen]

    new_articles = fetch_summaries(new_pmids) if new_pmids else []

    append_log(new_articles)

    seen.update(pmids)
    state["seen_pmids"] = sorted(seen)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"Rodada concluída: {len(new_articles)} artigo(s) novo(s) registrados em {LOG_FILE}")


if __name__ == "__main__":
    main()
