"""把模板库（JSON 目录）导入 MySQL —— 幂等、事务、可重复执行

    python -m db.import_templates --dir "D:\\明鉴智律公司\\ContractGeneration\\contract_generation\\template_library"

- 分类树：all_classify.json → contract_category_tree（整树）+ contract_category（叶子扁平化）
- 模板：每个 <ID>.json 中的 {"0": {...}, "1": {...}} → contract_template
- 幂等：按 (template_id, template_key) upsert，content_hash 未变化则跳过
- 原子：每个文件一个事务，单个文件损坏不影响其他文件；最后输出统计
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.repository.db import database_url, get_engine  # noqa: E402
from agent.repository.mysql_template_repo import template_params  # noqa: E402
from db.init_db import init_db  # noqa: E402


def leaf_categories(tree: dict, parent: str = "", level: int = 1, out=None):
    out = [] if out is None else out
    for k, v in tree.items():
        path = f"{parent}.{k}" if parent else k
        if isinstance(v, dict):
            if "ID" in v:
                out.append((path, k, v["ID"], level))
            children = {kk: vv for kk, vv in v.items() if kk != "ID" and isinstance(vv, dict)}
            if children:
                leaf_categories(children, path, level + 1, out)
    return out


def import_dir(url: str, lib_dir: Path) -> dict:
    from sqlalchemy import text
    init_db(url)
    eng = get_engine(url)
    stats = {"files": 0, "inserted": 0, "updated": 0, "unchanged": 0, "errors": [], "categories": 0}
    cls_path = lib_dir / "all_classify.json"
    if cls_path.exists():
        tree = json.loads(cls_path.read_text(encoding="utf-8"))
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM contract_category_tree"))
            conn.execute(text("INSERT INTO contract_category_tree (tree_json) VALUES (:t)"),
                         {"t": json.dumps(tree, ensure_ascii=False)})
            conn.execute(text("DELETE FROM contract_category"))
            for path, name, cid, level in leaf_categories(tree):
                conn.execute(text("INSERT INTO contract_category (path, name, category_id, level) "
                                  "VALUES (:p, :n, :c, :l)"), {"p": path[:255], "n": name[:128], "c": cid, "l": level})
                stats["categories"] += 1
    for f in sorted(lib_dir.glob("*.json")):
        if f.name == "all_classify.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{f.name}: {e}")
            continue
        stats["files"] += 1
        items = data.items() if isinstance(data, dict) and all(str(k).isdigit() for k in data) else [("0", data)]
        try:
            with eng.begin() as conn:
                for key, tpl in items:
                    if not isinstance(tpl, dict) or "template_name" not in tpl:
                        continue
                    tid = tpl.get("template_id") or f.stem
                    params = template_params(tid, int(key), tpl)
                    h = hashlib.sha1((params["content_json"] + params["template_name"] + params["abstract"])
                                     .encode("utf-8")).hexdigest()
                    params["content_hash"] = h
                    row = conn.execute(text("SELECT id, content_hash FROM contract_template "
                                            "WHERE template_id=:t AND template_key=:k"),
                                       {"t": tid, "k": int(key)}).fetchone()
                    if row is None:
                        conn.execute(text(
                            "INSERT INTO contract_template (template_id, template_key, template_name, contract_type, "
                            "party_type, scene, complexity, region, template_version, source_file, abstract, "
                            "content_json, placeholder_json, content_hash, is_recommended) VALUES (:template_id, "
                            ":template_key, :template_name, :contract_type, :party_type, :scene, :complexity, :region, "
                            ":template_version, :source_file, :abstract, :content_json, :placeholder_json, "
                            ":content_hash, :is_recommended)"), params)
                        stats["inserted"] += 1
                    elif row[1] != h:
                        conn.execute(text(
                            "UPDATE contract_template SET template_name=:template_name, contract_type=:contract_type, "
                            "party_type=:party_type, scene=:scene, complexity=:complexity, region=:region, "
                            "template_version=:template_version, source_file=:source_file, abstract=:abstract, "
                            "content_json=:content_json, placeholder_json=:placeholder_json, "
                            "content_hash=:content_hash, is_recommended=:is_recommended "
                            "WHERE template_id=:template_id AND template_key=:template_key"), params)
                        stats["updated"] += 1
                    else:
                        stats["unchanged"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{f.name}: {type(e).__name__}: {str(e)[:200]}")
    with eng.connect() as conn:
        stats["total_in_db"] = int(conn.execute(text("SELECT COUNT(*) FROM contract_template")).scalar())
    eng.dispose()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="模板库目录（含 all_classify.json 与 *.json）")
    ap.add_argument("--url", default=None)
    a = ap.parse_args()
    u = a.url or database_url()
    if not u:
        sys.exit("请设置 DATABASE_URL 或 MYSQL_* 环境变量，或使用 --url")
    print(json.dumps(import_dir(u, Path(a.dir)), ensure_ascii=False, indent=2))
