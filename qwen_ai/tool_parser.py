"""Tool parser for Qwen AI"""

import json
import re
from typing import List, Dict, Optional, Tuple

class ToolParser:
    """Tool parser for Qwen AI"""
    
    @staticmethod
    def has_tool_use(content: str) -> bool:
        """Check if content contains tool use

        Args:
            content: Content string

        Returns:
            bool: True if content contains tool use
        """
        # Check for standard formats
        if '<function_calls>' in content or '<tool_use>' in content:
            return True

        # Check for simplified XML format: <tool_name>{...}</tool_name>
        # Use DOTALL flag to match across newlines, and non-greedy matching
        simplified_pattern = r'\<(\w+)\>\s*\{.*?\}\s*\</\1\>'
        matches = re.findall(simplified_pattern, content, re.DOTALL)
        for name in matches:
            if name.lower() not in ['function_calls', 'call']:
                return True

        # Check for XML attribute format: <tag_name attr="value"></tag_name>
        attr_pattern = r'\<(\w+)\s+([^>]*)\>\s*\</\1\>'
        if re.search(attr_pattern, content):
            return True

        return False
    
    @staticmethod
    def parse_tool_use(content: str) -> Optional[List[Dict]]:
        """Parse tool use from content
        
        Args:
            content: Content string
        
        Returns:
            Optional[List[Dict]]: List of tool calls
        """
        tool_calls = []
        
        # Parse XML format: <function_calls><call:name>{"arg": "value"}</call></function_calls>
        if '<function_calls>' in content:
            pattern = r'\<call:(\w+)\>(\{[^\}]+\})\</call\>'
            matches = re.findall(pattern, content)
            for i, (name, args) in enumerate(matches):
                try:
                    tool_calls.append({
                        'id': f'tool_{i}',
                        'function': {
                            'name': name,
                            'arguments': args
                        }
                    })
                except json.JSONDecodeError:
                    continue
        
        # Parse XML format: <tool_use><name>name</name><arguments>...</arguments></tool_use>
        if '<tool_use>' in content:
            pattern = r'<tool_use>.*?<name>([^<]+)</name>.*?<arguments>([^<]+)</arguments>.*?</tool_use>'
            matches = re.findall(pattern, content, re.DOTALL)
            for i, (name, args) in enumerate(matches):
                try:
                    tool_calls.append({
                        'id': f'tool_{i}',
                        'function': {
                            'name': name.strip(),
                            'arguments': args.strip()
                        }
                    })
                except json.JSONDecodeError:
                    continue
        
        # Fallback: Check for simplified XML format: <tool_name>{...}</tool_name>
        # This handles cases where Qwen uses <todo_write>...</todo_write> instead of <function_calls><call:todo_write>...</call></function_calls>
        if not tool_calls:
            # Use DOTALL flag to match across newlines, and non-greedy matching
            simplified_pattern = r'\<(\w+)\>\s*(\{.*?\})\s*\</\1\>'
            matches = re.findall(simplified_pattern, content, re.DOTALL)
            for i, (name, args) in enumerate(matches):
                # Skip if this looks like regular markdown or non-tool brackets
                if name.lower() in ['function_calls', 'call']:
                    continue
                try:
                    # Verify it's valid JSON
                    json.loads(args)
                    tool_calls.append({
                        'id': f'tool_{i}',
                        'function': {
                            'name': name,
                            'arguments': args
                        }
                    })
                except json.JSONDecodeError:
                    continue

        # Parse XML attribute format: <tag_name attr="value"></tag_name>
        if not tool_calls:
            attr_pattern = r'\<(\w+)\s+([^>]*)\>\s*\</\1\>'
            for match in re.finditer(attr_pattern, content):
                name = match.group(1)
                attrs_str = match.group(2)
                # Parse attributes like plan="value"
                attr_dict = {}
                for attr_match in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
                    attr_dict[attr_match.group(1)] = attr_match.group(2)
                if attr_dict:
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {
                            'name': name,
                            'arguments': json.dumps(attr_dict)
                        }
                    })

        return tool_calls if tool_calls else None
    
    @staticmethod
    def tools_to_system_prompt(tools: List[Dict]) -> str:
        """Convert tools to system prompt
        
        Args:
            tools: List of tools
        
        Returns:
            str: System prompt
        """
        if not tools:
            return ''
        
        tool_definitions = []
        for tool in tools:
            func = tool.get('function', {})
            name = func.get('name', '')
            description = func.get('description', '')
            parameters = func.get('parameters', {})
            
            params_str = json.dumps(parameters, indent=2)
            tool_definitions.append(f"Tool `{name}`: {description}. Arguments JSON schema: {params_str}")
        
        prompt = f"""## Available Tools

You have access to the following tools. Use them when needed.

CRITICAL: Tool names are CASE-SENSITIVE. Use exact names as listed below.

{chr(10).join(tool_definitions)}

## ⚠️ MANDATORY Tool Call Format — NEVER Deviate

You MUST use this EXACT format every single time you call a tool, no exceptions:

<function_calls>
<call:exact_tool_name>{{"argument": "value"}}</call>
</function_calls>

### Format Rules (MEMORIZE THESE):
1. Outer wrapper is ALWAYS `<function_calls>` and `</function_calls>`
2. Each tool call is ALWAYS `<call:tool_name>{{...json...}}</call>` — all on ONE LINE
3. JSON arguments go DIRECTLY after `<call:tool_name>` with NO newline
4. The `</call>` closes IMMEDIATELY after the JSON — no space, no newline
5. Output NOTHING before or after the `<function_calls>` block when calling tools
6. NEVER describe what you're doing — just output the block and STOP
7. NEVER simulate tool results — wait for the actual result to be returned to you
8. NEVER use any other format even if you see different formats in conversation history
9. NEVER forget this format no matter how long the conversation gets
10. If you need to call multiple tools, put multiple `[call:...]` inside ONE `<function_calls>` block

### ✅ CORRECT Example:
<function_calls>
<call:read_file>{{"file_path": "C:\\\\path\\\\to\\\\file.js"}}</call>
</function_calls>

### ✅ CORRECT Multiple Tools:
<function_calls>
<call:read_file>{{"file_path": "C:\\\\path\\\\file1.js"}}</call>
<call:read_file>{{"file_path": "C:\\\\path\\\\file2.js"}}</call>
</function_calls>

### ❌ WRONG — Never do this:
- `<function_call>` (missing s)
- `<call:tool_name>\\n{{json}}` (JSON on separate line)
- Describing the tool call in text
- Writing "<tool_result "id"=".*">.*</tool_result>
- Using XML format like `<tool_use>`
- Using any format other than the one shown above

## 🔁 Reminder (Re-read this before every tool call):
The ONLY valid format is:
<function_calls>
<call:TOOL_NAME>{{"key": "value"}}</call>
</function_calls>"""
        
        return prompt
    
    @staticmethod
    def format_tool_result(tool_call_id: str, tool_name: str, result: str) -> str:
        """Format tool result
        
        Args:
            tool_call_id: Tool call ID
            tool_name: Tool name
            result: Tool result
        
        Returns:
            str: Formatted tool result
        """
        return f"""Tool call result for {tool_name}:

{result}
"""