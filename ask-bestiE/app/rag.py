import os
from typing import List, Dict, Tuple

import numpy as np
import pandas as pd  # Excel support (requires openpyxl)
from sentence_transformers import SentenceTransformer


# ---- Helper: normalize headers safely ----
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    def norm(s: str) -> str:
        return " ".join(str(s).strip().lower().replace("_", " ").split())
    df = df.copy()
    df.rename(columns={c: norm(c) for c in df.columns}, inplace=True)
    return df


class LocalVectorStore:
    """
    Excel-only reader.

    Expected columns (case-insensitive, spaces/underscores tolerated):
      - Service Number
      - Service Name
      - Description
      - Portal URL
      - Request Type
      - Request Description
      - Request URL

    Each Excel row becomes a searchable item:
      title       = Request Type
      url         = Request URL        (deep link to section/form)
      portal_url  = Portal URL         (portal home)
      service     = Service Name       (portal display name)
      content     = "{Request Type} — {Request Description}. Service: {Service Name}. {Description}"
    """

    def __init__(self, seed_dir: str, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.seed_dir = seed_dir
        self.xlsx_path = os.path.join(seed_dir, "help_docs.xlsx")
        if not os.path.exists(self.xlsx_path):
            raise FileNotFoundError(f"Excel not found: {self.xlsx_path}")

        self.model = SentenceTransformer(model_name)
        self.rows: List[Dict] = []
        self.doc_embeddings = None

        self._load_excel()
        self._build_embeddings()

    # ---------- loading ----------
    def _load_excel(self):
        df = pd.read_excel(self.xlsx_path)
        df = _norm_cols(df)

        required = [
            "service number",
            "service name",
            "description",
            "portal url",
            "request type",
            "request description",
            "request url",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required column(s) in Excel: {missing}")

        # Trim whitespace; coerce NaNs to empty strings
        for c in required:
            df[c] = df[c].astype(str).where(~df[c].isna(), "")

        # Build normalized rows
        normalized: List[Dict] = []
        for _, r in df.iterrows():
            service      = r["service name"].strip()
            portal_url   = r["portal url"].strip()
            req_type     = r["request type"].strip()
            req_desc     = r["request description"].strip()
            req_url      = r["request url"].strip()
            svc_desc     = r["description"].strip()

            title = req_type or service or "Request"
            content = f"{req_type} — {req_desc}. Service: {service}. {svc_desc}".strip()

            normalized.append({
                "service_number": r["service number"].strip(),
                "service": service,
                "portal_url": portal_url,
                "title": title,
                "url": req_url,
                "content": content,
            })

        # ---- Backfill missing service names (if any) ----
        # Strategy: for any row with empty service, try to copy from another row
        # that shares the same portal_url; if that fails, copy from any row with same service_number.
        by_portal = {}
        by_svcnum = {}
        for row in normalized:
            pu = row.get("portal_url", "")
            sn = row.get("service_number", "")
            if pu:
                by_portal.setdefault(pu, []).append(row)
            if sn:
                by_svcnum.setdefault(sn, []).append(row)

        for row in normalized:
            if not row.get("service"):
                pu = row.get("portal_url", "")
                sn = row.get("service_number", "")
                # Try portal group first
                if pu and pu in by_portal:
                    for cand in by_portal[pu]:
                        svc = cand.get("service", "").strip()
                        if svc:
                            row["service"] = svc
                            break
                # Then service number group
                if not row.get("service") and sn and sn in by_svcnum:
                    for cand in by_svcnum[sn]:
                        svc = cand.get("service", "").strip()
                        if svc:
                            row["service"] = svc
                            break

        self.rows = normalized

    def _build_embeddings(self):
        texts = [f"{r.get('title','')} | {r.get('content','')}" for r in self.rows]
        self.doc_embeddings = self.model.encode(texts, normalize_embeddings=True)

    # ---------- retrieval ----------
    def top_k(self, query: str, k: int = 3) -> List[Tuple[float, Dict]]:
        q = self.model.encode([query], normalize_embeddings=True)[0]
        sims = np.dot(self.doc_embeddings, q)
        idxs = np.argsort(-sims)[:k]
        return [(float(sims[i]), self.rows[i]) for i in idxs]