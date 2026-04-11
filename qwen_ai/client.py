"""Qwen AI Client - OpenAI compatible interface"""

from typing import List, Dict, Optional, Generator, Any
from .adapter import QwenAiAdapter
from .stream_handler import QwenAiStreamHandler
from .tool_parser import ToolParser

class QwenAiClient:
    """Qwen AI Client with OpenAI compatible interface"""
    
    def __init__(self, token: str, cookies: Optional[str] = None):
        """Initialize Qwen AI Client
        
        Args:
            token: JWT token from chat.qwen.ai Local Storage
            cookies: Optional cookies string for enhanced compatibility
        """
        self.adapter = QwenAiAdapter(token, cookies)
    
    def chat_completions(self, model: str, messages: List[Dict], stream: bool = False, 
                        temperature: Optional[float] = None, tools: Optional[List[Dict]] = None,
                        enable_thinking: Optional[bool] = None, thinking_budget: Optional[int] = None) -> Any:
        """Chat completions API
        
        Args:
            model: Model name
            messages: List of messages
            stream: Whether to use streaming
            temperature: Temperature setting
            tools: List of tools for function calling
            enable_thinking: Whether to enable thinking mode
            thinking_budget: Thinking budget
        
        Returns:
            Generator for streaming, dict for non-streaming
        """
        # Process tools if provided
        processed_messages = messages.copy()
        if tools:
            # Check if system prompt already has tool definitions
            has_tool_prompt = any(
                msg.get('role') == 'system' and 
                ('Available Tools' in msg.get('content', '') or '<tools>' in msg.get('content', ''))
                for msg in messages
            )
            
            if not has_tool_prompt:
                # Add tool definitions to system prompt
                tool_prompt = ToolParser.tools_to_system_prompt(tools)
                
                # Find or create system message
                system_messages = [msg for msg in processed_messages if msg.get('role') == 'system']
                if system_messages:
                    system_messages[0]['content'] = system_messages[0]['content'] + '\n\n' + tool_prompt
                else:
                    processed_messages.insert(0, {'role': 'system', 'content': tool_prompt})
        
        # Make request
        response, chat_id, parent_id = self.adapter.chat_completion(
            model=model,
            messages=processed_messages,
            stream=stream,
            temperature=temperature,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget
        )
        
        # Create stream handler
        handler = QwenAiStreamHandler(model, lambda cid: self.adapter.delete_chat(cid))
        handler.set_chat_id(chat_id)
        
        if stream:
            return handler.handle_stream(response)
        else:
            return handler.handle_non_stream(response)
    
    def create_chat(self, model: str, title: str = 'New Chat') -> str:
        """Create a new chat
        
        Args:
            model: Model name
            title: Chat title
        
        Returns:
            str: Chat ID
        """
        model_id = self.adapter.map_model(model)
        return self.adapter.create_chat(model_id, title)
    
    def delete_chat(self, chat_id: str) -> bool:
        """Delete a chat
        
        Args:
            chat_id: Chat ID
        
        Returns:
            bool: True if deletion was successful
        """
        return self.adapter.delete_chat(chat_id)
    
    def delete_all_chats(self) -> bool:
        """Delete all chats
        
        Returns:
            bool: True if deletion was successful
        """
        return self.adapter.delete_all_chats()
