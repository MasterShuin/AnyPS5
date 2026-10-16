import sys
sys.path.insert(0, r'C:\PROGRAMMAZIONE\REPOSITORY\AnyPS5\tools')
from import_audit import main
sys.exit(main([
    '--libs', r'C:\Users\lupol\Desktop\game\AnyPS5_Output\libs',
    r'C:\Users\lupol\Desktop\game\AnyPS5_Output\game.registry.json'
]))