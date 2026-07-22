from pathlib import Path

path = Path("scripts/apply_xflarxx_account_access_cleanup.py")
text = path.read_text(encoding="utf-8")
section = text.find("# 2. Run PART5/PART6 in the normal serialized auto-participation workflow.")
first = text.find("replace_once(\n", section)
second = text.find("replace_once(\n", first + 1)
if section < 0 or first < 0 or second < 0:
    raise SystemExit("installer PART5/PART6 section boundaries not found")
replacement = '''workflow_path = ".github/workflows/auto-participation.yml"
workflow_text = read(workflow_path)
validation_marker = \'''          BETBOOM_STORAGE_STATE_JSON_PART3: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART3 }}
          BETBOOM_STORAGE_STATE_JSON_PART4: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART4 }}
          BETBOOM_ACCOUNT2_LABEL: "Аккаунт 2"
\'''
validation_replacement = \'''          BETBOOM_STORAGE_STATE_JSON_PART3: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART3 }}
          BETBOOM_STORAGE_STATE_JSON_PART4: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART4 }}
          BETBOOM_STORAGE_STATE_JSON_PART5: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART5 }}
          BETBOOM_STORAGE_STATE_JSON_PART6: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART6 }}
          BETBOOM_ACCOUNT2_LABEL: "Аккаунт 2"
\'''
position = workflow_text.find(validation_marker)
if position < 0:
    raise RuntimeError("auto-participation validation environment marker not found")
workflow_text = (
    workflow_text[:position]
    + validation_replacement
    + workflow_text[position + len(validation_marker):]
)
write(workflow_path, workflow_text)

'''
path.write_text(text[:first] + replacement + text[second:], encoding="utf-8")
