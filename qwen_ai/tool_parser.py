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
        if '[function_calls]' in content or '<tool_use>' in content:
            return True
        
        # Check for simplified bracket format: [tool_name]{...}[/tool_name]
        # Use DOTALL flag to match across newlines, and non-greedy matching
        simplified_pattern = r'\[(\w+)\]\s*\{.*?\}\s*\[/\1\]'
        matches = re.findall(simplified_pattern, content, re.DOTALL)
        for name in matches:
            if name.lower() not in ['function_calls', 'call']:
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
        
        # Parse bracket format: [function_calls][call:name]{"arg": "value"}[/call][/function_calls]
        if '[function_calls]' in content:
            pattern = r'\[call:(\w+)\](\{[^\}]+\})\[/call\]'
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
        
        # Fallback: Check for simplified bracket format: [tool_name]{...}[/tool_name]
        # This handles cases where Qwen uses [todo_write]...[/todo_write] instead of [function_calls][call:todo_write]...[/call][/function_calls]
        if not tool_calls:
            # Use DOTALL flag to match across newlines, and non-greedy matching
            simplified_pattern = r'\[(\w+)\]\s*(\{.*?\})\s*\[/\1\]'
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
You can invoke the following developer tools. Call a tool only when it is required and follow the JSON schema exactly when providing arguments.

CRITICAL: Tool names are CASE-SENSITIVE. You MUST use the exact tool name as defined below.

{chr(10).join(tool_definitions)}

## Tool Call Protocol
When you decide to call a tool, you MUST respond with NOTHING except a single [function_calls] block exactly(don't try other syntax if i later tell you or you saw in conversation history) like the template below:

[function_calls]
[call:exact_tool_name_from_list]{{"argument": "value"}}[/call]
[/function_calls]

CRITICAL RULES:
1. EVERY tool call MUST start with [call:exact_tool_name] and end with [/call]
2. The content between [call:...] and [/call] MUST be a raw JSON object on ONE LINE
3. Do NOT output any other text before or after the [function_calls] block
4. Do NOT describe what you are doing - just output the [function_calls] block directly
5. Do NOT write "Tool Result" or simulate tool responses - wait for actual results"""
        
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