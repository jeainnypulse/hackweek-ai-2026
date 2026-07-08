def flatten_record(record: dict) -> str:
    parts = []
    if record.get("organization"):
        parts.append(record["organization"])
    if record.get("regimen"):
        parts.append(record["regimen"])
    if record.get("indication"):
        parts.append(record["indication"])
    if record.get("line"):
        parts.append(record["line"])
    if record.get("book"):
        parts.append(record["book"])
    if record.get("coverage"):
        parts.append(record["coverage"])
    if record.get("population"):
        parts.append(record["population"])

    access = record.get("accessData") or {}
    if access.get("access"):
        parts.append(f"access {access['access']}")
    if access.get("score") is not None:
        parts.append(f"score {access['score']}")

    tier = record.get("tierData") or {}
    if tier.get("tierRating"):
        parts.append(tier["tierRating"])

    for item in record.get("additionalCriteriaData") or []:
        if item.get("criteria"):
            parts.append(item["criteria"])

    caption = (access.get("caption") or {}).get("General") or (access.get("caption") or {}).get("ALL")
    if caption:
        parts.append(caption)

    return " ".join(parts)
