from pathlib import Path

path = Path("scripts/apply_xflarxx_account_access_cleanup.py")
text = path.read_text(encoding="utf-8")
section = text.find("# 2. Run PART5/PART6 in the normal serialized auto-participation workflow.")
positions: list[int] = []
cursor = section
for _index in range(4):
    cursor = text.find("replace_once(\n", cursor + 1)
    positions.append(cursor)
if section < 0 or any(position < 0 for position in positions):
    raise SystemExit("installer PART5/PART6 section boundaries not found")
first, _second, _third, fourth = positions
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
preflight_marker = \'''          if not betboom_account_participation.configured():
              raise SystemExit("Vyacheslav second BetBoom session PART3/PART4 is not configured")
\'''
preflight_replacement = preflight_marker + \'''          if not betboom_account_participation.xflarxx_configured():
              raise SystemExit("xFLARXx BetBoom session PART5/PART6 is not configured")
\'''
if workflow_text.count(preflight_marker) != 1:
    raise RuntimeError("auto-participation secondary-account preflight marker not found")
workflow_text = workflow_text.replace(preflight_marker, preflight_replacement, 1)
old_message = '          print("Auto participation preflight OK for both BetBoom accounts")\\n'
new_message = '          print("Auto participation preflight OK for all configured BetBoom accounts")\\n'
if workflow_text.count(old_message) != 1:
    raise RuntimeError("auto-participation preflight message marker not found")
workflow_text = workflow_text.replace(old_message, new_message, 1)
write(workflow_path, workflow_text)

'''
path.write_text(text[:first] + replacement + text[fourth:], encoding="utf-8")
