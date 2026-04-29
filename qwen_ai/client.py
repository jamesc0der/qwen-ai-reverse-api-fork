"""Qwen AI Client - OpenAI compatible interface"""

from typing import List, Dict, Optional, Generator, Any
from .adapter import QwenAiAdapter
from .stream_handler import QwenAiStreamHandler
from .tool_parser import ToolParser
from .debug_logger import log_raw, log_exception

class QwenAiClient:
    """Qwen AI Client with OpenAI compatible interface"""
    
    def __init__(self, token: str, cookies: Optional[str] = None):
        """Initialize Qwen AI Client

        Args:
            token: JWT token from chat.qwen.ai Local Storage
            cookies: Optional cookies string for enhanced compatibility
        """
        log_raw("DEBUG", "CLIENT", "Initializing QwenAiClient")
        self.adapter = QwenAiAdapter(token, cookies)
        log_raw("DEBUG", "CLIENT", "QwenAiClient initialized successfully")
    
    def chat_completions(self, model: str, messages: List[Dict], stream: bool = False,
                        temperature: Optional[float] = None, tools: Optional[List[Dict]] = None,
                        enable_thinking: Optional[bool] = None, thinking_budget: Optional[int] = None,
                        auto_delete_chat: bool = False) -> Any:
        """Chat completions API

        Args:
            model: Model name
            messages: List of messages
            stream: Whether to use streaming
            temperature: Temperature setting
            tools: List of tools for function calling
            enable_thinking: Whether to enable thinking mode
            thinking_budget: Thinking budget
            auto_delete_chat: Whether to delete the chat after completion

        Returns:
            Generator for streaming, dict for non-streaming
        """
        log_raw("DEBUG", "CLIENT", f"Chat completions request: model={model}, stream={stream}, messages={len(messages)}, tools={'yes' if tools else 'no'}")
        processed_messages = messages.copy()
        if tools:
            has_tool_prompt = any(
                msg.get('role') == 'system' and
                ('Available Tools' in msg.get('content', '') or '<tools>' in msg.get('content', ''))
                for msg in messages
            )

            if not has_tool_prompt:
                log_raw("DEBUG", "CLIENT", f"Adding tool prompt for {len(tools)} tools")
                tool_prompt = ToolParser.tools_to_system_prompt(tools)

                system_messages = [msg for msg in processed_messages if msg.get('role') == 'system']
                if system_messages:
                    system_messages[0]['content'] = system_messages[0]['content'] + '\n\n' + tool_prompt
                    log_raw("DEBUG", "CLIENT", "Tool prompt appended to existing system message")
                else:
                    processed_messages.insert(0, {'role': 'system', 'content': tool_prompt})
                    log_raw("DEBUG", "CLIENT", "Tool prompt inserted as new system message")

        response, chat_id, parent_id = self.adapter.chat_completion(
            model=model,
            messages=processed_messages,
            stream=stream,
            temperature=temperature,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            auto_delete_chat=auto_delete_chat
        )

        log_raw("DEBUG", "CLIENT", f"Creating stream handler for chat_id={chat_id}")
        handler = QwenAiStreamHandler(
            model,
            auto_delete_chat=auto_delete_chat,
            delete_chat_func=self.adapter.delete_chat,
            tools=tools
        )
        handler.set_chat_id(chat_id)

        if stream:
            log_raw("DEBUG", "CLIENT", "Starting streaming response")
            return handler.handle_stream(response)
        else:
            log_raw("DEBUG", "CLIENT", "Starting non-streaming response")
            return handler.handle_non_stream(response)
    
    def create_chat(self, model: str, title: str = 'New Chat') -> str:
        """Create a new chat

        Args:
            model: Model name
            title: Chat title

        Returns:
            str: Chat ID
        """
        log_raw("DEBUG", "CLIENT", f"Creating chat with model={model}, title={title}")
        model_id = self.adapter.map_model(model)
        chat_id = self.adapter.create_chat(model_id, title)
        log_raw("DEBUG", "CLIENT", f"Chat created successfully: {chat_id}")
        return chat_id
    
    def delete_chat(self, chat_id: str) -> bool:
        """Delete a chat

        Args:
            chat_id: Chat ID

        Returns:
            bool: True if deletion was successful
        """
        log_raw("DEBUG", "CLIENT", f"Deleting chat: {chat_id}")
        success = self.adapter.delete_chat(chat_id)
        log_raw("INFO", "CLIENT", f"Chat deletion result for {chat_id}: {'success' if success else 'failed'}")
        return success
    
    def delete_all_chats(self) -> bool:
        """Delete all chats

        Returns:
            bool: True if deletion was successful
        """
        log_raw("DEBUG", "CLIENT", "Deleting all chats")
        success = self.adapter.delete_all_chats()
        log_raw("INFO", "CLIENT", f"Delete all chats result: {'success' if success else 'failed'}")
        return success
