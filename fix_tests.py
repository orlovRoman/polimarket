import os
import re
from pathlib import Path

tests_dir = Path(r"c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\tests")

# We will read each test file and apply regex replacements

for file_path in tests_dir.glob("test_*.py"):
    content = file_path.read_text(encoding="utf-8")
    original = content

    # 1. replace `.estimate_market.return_value = (.*)` with `.estimate_market = AsyncMock(return_value=\1)`
    content = re.sub(r'\.estimate_market\.return_value\s*=\s*(.+)', r'.estimate_market = AsyncMock(return_value=\1)', content)
    
    # 2. replace `.estimate_market.side_effect = (.*)` with `.estimate_market = AsyncMock(side_effect=\1)`
    content = re.sub(r'\.estimate_market\.side_effect\s*=\s*(.+)', r'.estimate_market = AsyncMock(side_effect=\1)', content)
    
    # 3. Add `from unittest.mock import AsyncMock` if AsyncMock is used but not imported
    if "AsyncMock" in content and "from unittest.mock import AsyncMock" not in content and "import AsyncMock" not in content:
        content = "from unittest.mock import AsyncMock\n" + content
        
    # 4. Wrap run_agent_evaluation calls with asyncio.run
    # Some calls are multiline, e.g. `run_agent_evaluation(\n...)`
    # Let's find `run_agent_evaluation(` and prepend `asyncio.run(` and append `)` to the matching closing bracket.
    if "run_agent_evaluation(" in content:
        import ast
        try:
            # We can use AST to find calls to run_agent_evaluation, but simple string matching might be easier if we're careful.
            # Actually, `import asyncio` is needed.
            if "import asyncio" not in content:
                content = "import asyncio\n" + content
            
            # Simple replace is risky due to multiline, let's use a regex that matches balanced parentheses or just do a manual state machine for parentheses
            out = []
            i = 0
            while i < len(content):
                if content[i:].startswith("run_agent_evaluation("):
                    # Check if it is a def or import
                    line_start = content.rfind("\n", 0, i)
                    if line_start != -1:
                        line = content[line_start:i].strip()
                        if line.startswith("def ") or line.startswith("from ") or line.startswith("import ") or line.startswith("async def "):
                            out.append("run_agent_evaluation(")
                            i += len("run_agent_evaluation(")
                            continue
                    
                    # It's a call!
                    out.append("asyncio.run(run_agent_evaluation(")
                    i += len("run_agent_evaluation(")
                    
                    # now find matching closing parenthesis
                    parens = 1
                    while i < len(content) and parens > 0:
                        if content[i] == '(':
                            parens += 1
                        elif content[i] == ')':
                            parens -= 1
                        out.append(content[i])
                        i += 1
                    out.append(")") # close asyncio.run
                else:
                    out.append(content[i])
                    i += 1
            content = "".join(out)
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")

    # 5. Fix direct agent.estimate_market(...) calls
    if "agent.estimate_market(" in content or "scout.estimate_market(" in content or "swing.estimate_market(" in content:
        if "import asyncio" not in content:
            content = "import asyncio\n" + content
        
        for prefix in ["agent.estimate_market(", "scout.estimate_market(", "swing.estimate_market("]:
            out = []
            i = 0
            while i < len(content):
                if content[i:].startswith(prefix):
                    line_start = content.rfind("\n", 0, i)
                    if line_start != -1:
                        line = content[line_start:i].strip()
                        if line.startswith("def ") or line.startswith("from ") or line.startswith("import "):
                            out.append(prefix)
                            i += len(prefix)
                            continue
                            
                    out.append("asyncio.run(" + prefix)
                    i += len(prefix)
                    parens = 1
                    while i < len(content) and parens > 0:
                        if content[i] == '(':
                            parens += 1
                        elif content[i] == ')':
                            parens -= 1
                        out.append(content[i])
                        i += 1
                    out.append(")")
                else:
                    out.append(content[i])
                    i += 1
            content = "".join(out)

    if content != original:
        file_path.write_text(content, encoding="utf-8")
        print(f"Updated {file_path.name}")
