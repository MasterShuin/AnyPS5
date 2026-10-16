import subprocess
import sys
import glob
from pathlib import Path

output = Path(r'C:\Users\lupol\Desktop\game\AnyPS5_Output')
libs = output / 'libs'
audit_json = output / 'audit.json'

registries = sorted(output.glob('*.registry.json'))
print(f'Found {len(registries)} registry files:')
for r in registries:
    print(f'  {r.name}')

cmd = [sys.executable, str(Path('tools/import_audit.py')), *[str(r) for r in registries],
       '--libs', str(libs), '--json', str(audit_json)]
print(f'\nRunning: {" ".join(cmd)}')
print()

result = subprocess.run(cmd, capture_output=True, text=True)
print('STDOUT:')
print(result.stdout)
print('STDERR:')
print(result.stderr)
print(f'Return code: {result.returncode}')
