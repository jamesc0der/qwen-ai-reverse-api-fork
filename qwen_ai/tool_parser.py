"""Tool parser for Qwen AI"""

import json
import re
from typing import List, Dict, Optional, Tuple

from .debug_logger import log_raw, log_tool_parsed

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
        # Check for new format only
        if '§FUNC§' in content:
            # Check if we have a complete FUNC block
            start = content.find('§FUNC§')
            end = content.find('§END_FUNC§', start)
            if start != -1 and end != -1:
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

        # Parse new format: §FUNC§...§END_FUNC§
        if '§FUNC§' in content:
            # Find the FUNC block
            start = content.find('§FUNC§')
            end = content.find('§END_FUNC§', start)
            if start != -1 and end != -1:
                func_block = content[start + 11:end]  # Skip §FUNC§
                # Find all §CALL§...§END_CALL§ within
                call_start = 0
                i = 0
                while True:
                    start_idx = func_block.find('§CALL§', call_start)
                    if start_idx == -1:
                        break
                    end_idx = func_block.find('§END_CALL§', start_idx)
                    if end_idx == -1:
                        break

                    call_json = func_block[start_idx + 11:end_idx]  # Skip §CALL§
                    try:
                        call_data = json.loads(call_json)
                        name = call_data.get('name')
                        args_str = call_data.get('args')
                        if name and args_str:
                            tool_calls.append({
                                'id': f'tool_{i}',
                                'function': {
                                    'name': name,
                                    'arguments': args_str  # Keep as string, not parsed
                                }
                            })
                            i += 1
                    except json.JSONDecodeError:
                        pass

                    call_start = end_idx + 13  # Skip §END_CALL§

        return tool_calls if tool_calls else None
    
    @staticmethod
    def tools_to_system_prompt(tools: List[Dict]) -> str:
        if not tools:
            return ''
        
        tool_definitions = []
        for tool in tools:
            func = tool.get('function', {})
            name = func.get('name', '')
            description = func.get('description', '')
            parameters = func.get('parameters', {})
            params_str = json.dumps(parameters, indent=2)
            tool_definitions.append(
                f"- {name}: {description}\n  Parameters: {params_str}"
            )
        
        tools_list = '\n'.join(tool_definitions)
        
        return f"""## TOOLS

    You have access to these tools:
    {tools_list}

    ## TOOL CALL FORMAT

    To call a tool output this block — nothing before or after it:
    §FUNC§
    §CALL§{{"name":"tool_name", "args":"{{\\"arg\\": \\"value\\"}}"}}§END_CALL§
    §END_FUNC§

    Multiple tools go inside ONE §FUNC§ block as separate §CALL§ lines.

    ## ABSOLUTE RULES — NEVER BREAK THESE

    1. §END_FUNC§ is MANDATORY — never open §FUNC§ without closing it
    2. When calling tools — output the §FUNC§ block ONLY, then stop
    3. Wait silently for the system to return results before continuing
    4. Tool results are delivered by the system automatically — you never write them yourself
    5. If you are writing code and the code happens to mention a tool name — that is NOT a tool call, do not wrap it in §FUNC§
    6. Never repeat, echo, or summarize what the system gave you as a tool result
    7. Never output anything that looks like a system-injected block
    8. Your response after receiving results must be plain text or another §FUNC§ call — nothing else

    ## IMPORTANT

    You are operating as an AI assistant with tool access.
    The system handles all tool execution and result delivery invisibly.
    Your only job is: think, call tools when needed using the format above, then respond to the user.
    Do not narrate the tool calling process. Do not confirm you received results. Just act on them.
    """
    
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
        return f"""[Tool Result for {tool_name} {tool_call_id}]\n{result}\n[/Tool Result]"""