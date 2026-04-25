"""Qwen AI Stream Handler - Based on Chat2API logic"""

import json
import time
import re
from typing import Optional, Callable, Dict, Any, Generator
from http.client import IncompleteRead


class QwenAiStreamHandler:
    """Qwen AI Stream Handler"""
    
    def __init__(self, model: str, on_end: Optional[Callable[[str], None]] = None,
                 auto_delete_chat: bool = False, delete_chat_func: Optional[Callable[[str], bool]] = None,
                 tools: Optional[list[Dict]] = None):
        """Initialize stream handler
        
        Args:
            model: Model name
            on_end: Callback function when stream ends
            auto_delete_chat: Whether to auto delete chat after completion
            delete_chat_func: Function to delete chat (receives chat_id, returns bool)
            tools: List of tool definitions (if present, enables aggressive tool call detection)
        """
        self.chat_id = ''
        self.model = model
        self.created = int(time.time())
        self.on_end = on_end
        self.response_id = ''
        self.content = ''
        self.tool_calls_sent = False
        self.auto_delete_chat = auto_delete_chat
        self.delete_chat_func = delete_chat_func
        self.tools = tools  # Store tools to know if we should expect tool calls
    
    def set_chat_id(self, chat_id: str):
        """Set chat ID"""
        self.chat_id = chat_id
    
    def _handle_completion(self, chat_id: str):
        """Handle completion - call on_end callback and optionally delete chat"""
        if self.on_end:
            self.on_end(chat_id)
        if self.auto_delete_chat and self.delete_chat_func and chat_id:
            try:
                self.delete_chat_func(chat_id)
                print(f'[QwenAI] Auto-deleted chat: {chat_id}')
            except Exception as e:
                print(f'[QwenAI] Failed to auto-delete chat {chat_id}: {e}')
    
    def _parse_sse_line(self, line: str) -> Optional[str]:
        """Parse SSE line and return data"""
        if line.startswith('data: '):
            return line[6:]
        return None
    
    def handle_stream(self, response) -> Generator[str, None, None]:
        """Handle streaming response - direct line parsing like Chat2API
        
        Args:
            response: Response object with stream=True
        
        Yields:
            str: Server-sent events
        """
        reasoning_text = ''
        has_sent_reasoning = False
        summary_text = ''
        initial_chunk_sent = False
        content_buffer = ''
        detected_tool_call = False  # Track if we've detected a tool call in progress
        
        # If tools are provided, we assume any content might be a tool call until proven otherwise.
        # We buffer everything in the 'answer' phase and only flush if no tool call is detected by the end.
        expect_tools = self.tools is not None and len(self.tools) > 0
        
        try:
            # Direct line iteration like Chat2API - no sseclient buffering
            for line in response.iter_lines():
                if not line:
                    continue
                
                line_str = line.decode('utf-8')
                
                # Parse SSE data line
                if not line_str.startswith('data: '):
                    continue
                    
                data_str = line_str[6:]
                
                if data_str == '[DONE]':
                    continue
                
                try:
                    data = json.loads(data_str)
                    
                    # Handle response.created
                    if data.get('response.created', {}).get('response_id'):
                        self.response_id = data['response.created']['response_id']
                    
                    if not data.get('choices'):
                        continue
                    
                    choice = data['choices'][0]
                    delta = choice.get('delta', {})
                    phase = delta.get('phase')
                    status = delta.get('status')
                    content = delta.get('content', '')
                    
                    # Handle think phase
                    if phase == 'think':
                        if status != 'finished':
                            reasoning_text += content
                            if not has_sent_reasoning:
                                reasoning_start = {
                                    'id': self.response_id or self.chat_id,
                                    'model': self.model,
                                    'object': 'chat.completion.chunk',
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'role': 'assistant', 'reasoning_content': ''},
                                        'finish_reason': None,
                                    }],
                                    'created': self.created,
                                }
                                yield f'data: {json.dumps(reasoning_start)}\n\n'
                                has_sent_reasoning = True
                            if content:
                                reasoning_chunk = {
                                    'id': self.response_id or self.chat_id,
                                    'model': self.model,
                                    'object': 'chat.completion.chunk',
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'reasoning_content': content},
                                        'finish_reason': None,
                                    }],
                                    'created': self.created,
                                }
                                yield f'data: {json.dumps(reasoning_chunk)}\n\n'
                    
                    # Handle thinking_summary phase
                    elif phase == 'thinking_summary':
                        extra = delta.get('extra', {})
                        summary_thought = extra.get('summary_thought', {})
                        if summary_thought.get('content'):
                            new_summary = '\n'.join(summary_thought['content'])
                            if new_summary and len(new_summary) > len(summary_text):
                                diff = new_summary[len(summary_text):]
                                if diff:
                                    if not has_sent_reasoning:
                                        reasoning_start = {
                                            'id': self.response_id or self.chat_id,
                                            'model': self.model,
                                            'object': 'chat.completion.chunk',
                                            'choices': [{
                                                'index': 0,
                                                'delta': {'role': 'assistant', 'reasoning_content': ''},
                                                'finish_reason': None,
                                            }],
                                            'created': self.created,
                                        }
                                        yield f'data: {json.dumps(reasoning_start)}\n\n'
                                        has_sent_reasoning = True
                                    reasoning_chunk = {
                                        'id': self.response_id or self.chat_id,
                                        'model': self.model,
                                        'object': 'chat.completion.chunk',
                                        'choices': [{
                                            'index': 0,
                                            'delta': {'reasoning_content': diff},
                                            'finish_reason': None,
                                        }],
                                        'created': self.created,
                                    }
                                    yield f'data: {json.dumps(reasoning_chunk)}\n\n'
                                summary_text = new_summary
                    
                    # Handle answer phase
                    elif phase == 'answer':
                        if not initial_chunk_sent:
                            initial_chunk = {
                                'id': '',
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(initial_chunk)}\n\n'
                            initial_chunk_sent = True
                        
                        self.content += content
                        
                        # Check if we now have a tool call marker (standard or simplified format)
                        if not detected_tool_call:
                            if '[function_calls]' in self.content or '<tool_use>' in self.content:
                                detected_tool_call = True
                            else:
                                # Check for simplified format: [tool_name]{...}[/tool_name]
                                # We look for [name] followed by { or [ and ensure [/name] exists
                                simplified_pattern = r'\[(\w+)\]\s*(\{|\[)'
                                matches = re.findall(simplified_pattern, self.content)
                                for name, start_char in matches:
                                    closing_tag = f'[/{name}]'
                                    if closing_tag in self.content:
                                        detected_tool_call = True
                                        break
                        
                        # If we detected a tool call, buffer everything and suppress content
                        if detected_tool_call:
                            content_buffer += content
                        elif expect_tools:
                            # If tools are expected, temporarily buffer content to check for tool call patterns
                            # But still send content if it's clearly not a tool call after a reasonable amount of text
                            temp_check_content = content_buffer + content
                            
                            # Check if combined content contains tool call patterns
                            if self._has_tool_use(temp_check_content):
                                content_buffer += content
                            else:
                                # If it doesn't look like a tool call, send content immediately
                                content_chunk = {
                                    'id': self.response_id or self.chat_id,
                                    'model': self.model,
                                    'object': 'chat.completion.chunk',
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'content': content},
                                        'finish_reason': None,
                                    }],
                                    'created': self.created,
                                }
                                yield f'data: {json.dumps(content_chunk)}\n\n'
                        elif content:
                            # No tools expected
                            content_chunk = {
                                'id': self.response_id or self.chat_id,
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{
                                    'index': 0,
                                    'delta': {'content': content},
                                    'finish_reason': None,
                                }],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(content_chunk)}\n\n'
                    
                    # Handle phase is None but has content
                    elif phase is None and content:
                        if not initial_chunk_sent:
                            initial_chunk = {
                                'id': '',
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(initial_chunk)}\n\n'
                            initial_chunk_sent = True
                        
                        self.content += content
                        
                        # Check if we now have a tool call marker (standard or simplified format)
                        if not detected_tool_call:
                            if '[function_calls]' in self.content or '<tool_use>' in self.content:
                                detected_tool_call = True
                            else:
                                # Check for simplified format: [tool_name]{...}[/tool_name]
                                # We look for [name] followed by { or [ and ensure [/name] exists
                                simplified_pattern = r'\[(\w+)\]\s*(\{|\[)'
                                matches = re.findall(simplified_pattern, self.content)
                                for name, start_char in matches:
                                    closing_tag = f'[/{name}]'
                                    if closing_tag in self.content:
                                        detected_tool_call = True
                                        break
                        
                        # If we detected a tool call, buffer everything and suppress content
                        if detected_tool_call:
                            content_buffer += content
                        elif expect_tools:
                            # If tools are expected, buffer everything
                            content_buffer += content
                        elif content:
                            # No tools expected
                            content_chunk = {
                                'id': self.response_id or self.chat_id,
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{
                                    'index': 0,
                                    'delta': {'content': content},
                                    'finish_reason': None,
                                }],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(content_chunk)}\n\n'
                    
                    # Handle finished status
                    if status == 'finished' and (phase == 'answer' or phase is None):
                        # If we detected a tool call, generate tool calls and suppress all content
                        if detected_tool_call:
                            for chunk in self._generate_tool_calls():
                                yield chunk

                            # Yield final chunk with finish_reason for tool calls
                            final_chunk = {
                                'id': self.response_id or self.chat_id,
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(final_chunk)}\n\n'
                            yield 'data: [DONE]\n\n'
                            self._handle_completion(self.chat_id)
                            return

                        # If we were buffering because tools were expected, check if it was actually a tool call
                        if expect_tools and content_buffer:
                            if self._has_tool_use(self.content):
                                for chunk in self._generate_tool_calls():
                                    yield chunk

                                # Yield final chunk with finish_reason for tool calls
                                final_chunk = {
                                    'id': self.response_id or self.chat_id,
                                    'model': self.model,
                                    'object': 'chat.completion.chunk',
                                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                                    'created': self.created,
                                }
                                yield f'data: {json.dumps(final_chunk)}\n\n'
                                yield 'data: [DONE]\n\n'
                                self._handle_completion(self.chat_id)
                                return
                            else:
                                # Not a tool call, flush the buffer as content
                                content_chunk = {
                                    'id': self.response_id or self.chat_id,
                                    'model': self.model,
                                    'object': 'chat.completion.chunk',
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'content': content_buffer},
                                        'finish_reason': None,
                                    }],
                                    'created': self.created,
                                }
                                yield f'data: {json.dumps(content_chunk)}\n\n'
                        finish_reason = delta.get('finish_reason', 'stop')
                        final_chunk = {
                            'id': self.response_id or self.chat_id,
                            'model': self.model,
                            'object': 'chat.completion.chunk',
                            'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}],
                            'created': self.created,
                        }
                        yield f'data: {json.dumps(final_chunk)}\n\n'
                        yield 'data: [DONE]\n\n'
                        
                        self._handle_completion(self.chat_id)
                        return
                        
                except json.JSONDecodeError:
                    continue
                    
        except IncompleteRead:
            pass
        except Exception as e:
            print(f'[QwenAI] Stream error: {e}')
        finally:
            # Ensure we always send a finish reason and [DONE]
            if not self.tool_calls_sent:
                # If we detected a tool call, generate tool calls and suppress all content
                if detected_tool_call:
                    try:
                        for chunk in self._generate_tool_calls():
                            yield chunk
                    except Exception as e:
                        print(f'[QwenAI] Error generating tool calls: {e}')
                    return
                
                # If we were buffering because tools were expected, check one last time
                if expect_tools and content_buffer:
                    if self._has_tool_use(self.content):
                        try:
                            for chunk in self._generate_tool_calls():
                                yield chunk
                        except Exception as e:
                            print(f'[QwenAI] Error generating tool calls: {e}')
                        return
                    else:
                        # Flush buffer as content
                        if content_buffer:
                            content_chunk = {
                                'id': self.response_id or self.chat_id,
                                'model': self.model,
                                'object': 'chat.completion.chunk',
                                'choices': [{
                                    'index': 0,
                                    'delta': {'content': content_buffer},
                                    'finish_reason': None,
                                }],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(content_chunk)}\n\n'

                # Fallback: if no tool calls were detected and no content was sent,
                # ensure that any remaining content in self.content is returned
                if not self.tool_calls_sent and not detected_tool_call and not content_buffer and self.content:
                    # If we have content in self.content but didn't detect any tool calls,
                    # we should send it as regular content
                    content_chunk = {
                        'id': self.response_id or self.chat_id,
                        'model': self.model,
                        'object': 'chat.completion.chunk',
                        'choices': [{
                            'index': 0,
                            'delta': {'content': self.content},
                            'finish_reason': 'stop',
                        }],
                        'created': self.created,
                    }
                    yield f'data: {json.dumps(content_chunk)}\n\n'

                final_chunk = {
                    'id': self.response_id or self.chat_id,
                    'model': self.model,
                    'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                    'created': self.created,
                }
                yield f'data: {json.dumps(final_chunk)}\n\n'
                yield 'data: [DONE]\n\n'

                self._handle_completion(self.chat_id)
    
    def handle_non_stream(self, response) -> Dict[str, Any]:
        """Handle non-streaming response like Chat2API
        
        Args:
            response: Response object
        
        Returns:
            Dict[str, Any]: Response data
        """
        data = {
            'id': '',
            'model': self.model,
            'object': 'chat.completion',
            'choices': [
                {
                    'index': 0,
                    'message': {'role': 'assistant', 'content': '', 'reasoning_content': ''},
                    'finish_reason': 'stop',
                },
            ],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            'created': self.created,
        }
        
        reasoning_text = ''
        summary_text = ''
        
        try:
            # Direct line iteration - no sseclient
            for line in response.iter_lines():
                if not line:
                    continue
                
                line_str = line.decode('utf-8')
                
                if not line_str.startswith('data: '):
                    continue
                    
                data_str = line_str[6:]
                
                if data_str == '[DONE]':
                    break
                
                try:
                    parsed = json.loads(data_str)
                    
                    if parsed.get('response.created', {}).get('response_id'):
                        self.response_id = parsed['response.created']['response_id']
                        data['id'] = self.response_id
                    
                    if not parsed.get('choices'):
                        continue
                    
                    delta = parsed['choices'][0].get('delta', {})
                    phase = delta.get('phase')
                    status = delta.get('status')
                    content = delta.get('content', '')
                    
                    if phase == 'think' and status != 'finished':
                        reasoning_text += content
                    elif phase == 'thinking_summary':
                        extra = delta.get('extra', {})
                        summary_thought = extra.get('summary_thought', {})
                        if summary_thought.get('content'):
                            new_summary = '\n'.join(summary_thought['content'])
                            if new_summary and len(new_summary) > len(summary_text):
                                summary_text = new_summary
                    elif phase == 'answer':
                        if content:
                            data['choices'][0]['message']['content'] += content
                        if status == 'finished':
                            final_reasoning = reasoning_text or summary_text
                            if final_reasoning:
                                data['choices'][0]['message']['reasoning_content'] = final_reasoning
                            
                            self._handle_completion(self.chat_id)
                            return data
                    elif phase is None and content:
                        data['choices'][0]['message']['content'] += content
                        
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            print(f'[QwenAI] Non-stream error: {e}')
        
        # Finalize if stream ended without status=finished
        final_reasoning = reasoning_text or summary_text
        if final_reasoning:
            data['choices'][0]['message']['reasoning_content'] = final_reasoning
        
        # Check for tool calls in content
        content = data['choices'][0]['message']['content']
        if self._has_tool_use(content):
            tool_calls = self._parse_tool_use(content)
            if tool_calls:
                data['choices'][0]['message']['content'] = None
                data['choices'][0]['message']['tool_calls'] = [
                    {
                        'id': tc['id'],
                        'type': 'function',
                        'function': {
                            'name': tc['function']['name'],
                            'arguments': tc['function']['arguments'],
                        }
                    } for tc in tool_calls
                ]
                data['choices'][0]['finish_reason'] = 'tool_calls'
        # elif self._is_json_tool_call(content):
        #     try:
        #         json_data = json.loads(content.strip())
        #         tool_name = json_data.get('name', '')
        #         arguments = json_data.get('arguments', json_data.get('parameters', {}))
        #         if isinstance(arguments, dict):
        #             arguments = json.dumps(arguments)
                
        #         data['choices'][0]['message']['content'] = None
        #         data['choices'][0]['message']['tool_calls'] = [{
        #             'id': f'call_{int(time.time())}',
        #             'type': 'function',
        #             'function': {
        #                 'name': tool_name,
        #                 'arguments': arguments,
        #             }
        #         }]
        #         data['choices'][0]['finish_reason'] = 'tool_calls'
        #     except json.JSONDecodeError:
        #         pass
        
        self._handle_completion(self.chat_id)
        
        return data
    
    def _has_tool_use(self, content: str) -> bool:
        """Check if content contains tool use"""
        # Check for standard formats
        if '[function_calls]' in content or '<tool_use>' in content:
            return True
        
        # Check for simplified bracket format: [tool_name]{...}[/tool_name]
        # We require the content inside to start with { or [ to be considered a tool call
        # This prevents matching markdown links like [text](url) or random bracketed words
        simplified_pattern = r'\[(\w+)\]\s*(\{|\[)'
        matches = re.findall(simplified_pattern, content)
        for name, start_char in matches:
            # Ensure it has a closing tag
            closing_tag = f'[/{name}]'
            if closing_tag in content:
                return True
        
        return False
    
    def _generate_tool_calls(self):
        """Generate tool calls chunks"""
        tool_calls = self._parse_tool_use(self.content)
        if tool_calls:
            self.tool_calls_sent = True
            
            # Send tool_calls delta
            for i, tc in enumerate(tool_calls):
                chunk = {
                    'id': self.response_id or self.chat_id,
                    'model': self.model,
                    'object': 'chat.completion.chunk',
                    'choices': [{
                        'index': 0,
                        'delta': {
                            'tool_calls': [{
                                'index': i,
                                'id': tc['id'],
                                'type': 'function',
                                'function': {
                                    'name': tc['function']['name'],
                                    'arguments': tc['function']['arguments'],
                                },
                            }],
                        },
                        'finish_reason': None,
                    }],
                    'created': self.created,
                }
                yield f'data: {json.dumps(chunk)}\n\n'
            
            # Send finish with tool_calls
            finish_chunk = {
                'id': self.response_id or self.chat_id,
                'model': self.model,
                'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
                'created': self.created,
            }
            yield f'data: {json.dumps(finish_chunk)}\n\n'
            yield 'data: [DONE]\n\n'
            
            self._handle_completion(self.chat_id)
    
    def _is_json_tool_call(self, content: str) -> bool:
        """Check if content is a raw JSON tool call (Qwen native format)"""
        content = content.strip()
        if not content.startswith('{'):
            return False
        try:
            data = json.loads(content)
            return 'name' in data and ('arguments' in data or 'parameters' in data)
        except json.JSONDecodeError:
            return False

    def _generate_json_tool_calls(self, content: str):
        """Generate tool calls chunks from raw JSON content"""
        try:
            data = json.loads(content.strip())
            tool_name = data.get('name', '')
            # Qwen might use 'arguments' or 'parameters'
            arguments = data.get('arguments', data.get('parameters', {}))
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            
            self.tool_calls_sent = True
            
            # Send tool_calls delta
            chunk = {
                'id': self.response_id or self.chat_id,
                'model': self.model,
                'object': 'chat.completion.chunk',
                'choices': [{
                    'index': 0,
                    'delta': {
                        'tool_calls': [{
                            'index': 0,
                            'id': f'call_{int(time.time())}',
                            'type': 'function',
                            'function': {
                                'name': tool_name,
                                'arguments': arguments,
                            },
                        }],
                    },
                    'finish_reason': None,
                }],
                'created': self.created,
            }
            yield f'data: {json.dumps(chunk)}\n\n'
            
            # Send finish with tool_calls
            finish_chunk = {
                'id': self.response_id or self.chat_id,
                'model': self.model,
                'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
                'created': self.created,
            }
            yield f'data: {json.dumps(finish_chunk)}\n\n'
            yield 'data: [DONE]\n\n'
            
            self._handle_completion(self.chat_id)
        except json.JSONDecodeError:
            pass

    def _parse_tool_use(self, content: str) -> Optional[list]:
        """Parse tool use from content"""
        tool_calls = []
        
        # 1. Standard Format: [function_calls][call:name]{...}[/call][/function_calls]
        if '[function_calls]' in content:
            # Use DOTALL to match across newlines
            pattern = r'\[call:(\w+)\](.*?)\[/call\]'
            matches = re.findall(pattern, content, re.DOTALL)
            for name, args_str in matches:
                # Clean up the args string - it might contain newlines or extra whitespace
                args_clean = args_str.strip()
                try:
                    # Validate JSON
                    json.loads(args_clean)
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {
                            'name': name,
                            'arguments': args_clean
                        }
                    })
                except json.JSONDecodeError:
                    # If it's not valid JSON, it might be a partial stream or malformed
                    # We can try to extract JSON if it's embedded in text, but for now skip
                    continue
            if tool_calls:
                return tool_calls

        # 2. XML Format: <tool_use><name>name</name><arguments>...</arguments></tool_use>
        if '<tool_use>' in content:
            pattern = r'<tool_use>.*?<name>([^<]+)</name>.*?<arguments>(.*?)</arguments>.*?</tool_use>'
            matches = re.findall(pattern, content, re.DOTALL)
            for name, args_str in matches:
                args_clean = args_str.strip()
                try:
                    json.loads(args_clean)
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {
                            'name': name.strip(),
                            'arguments': args_clean
                        }
                    })
                except json.JSONDecodeError:
                    continue
            if tool_calls:
                return tool_calls
        
        # 3. Simplified Format: [tool_name]{...}[/tool_name]
        # We only match if the content inside is a JSON object or array to avoid false positives
        # like markdown links [text](url) or natural language [word].
        # Updated regex to be more precise and handle potential whitespace/newlines better
        # Removed $ anchor to allow matching in multi-line streams
        simplified_pattern = r'\[(\w+)\]\s*(\{.*?\}|\[.*?\])\s*\[/\1\]'
        matches = re.findall(simplified_pattern, content, re.DOTALL)
        
        for name, args_str in matches:
            # Skip standard tags we already handled
            if name.lower() in ['function_calls', 'call']:
                continue
                
            args_clean = args_str.strip()
            try:
                # Validate JSON
                parsed_json = json.loads(args_clean)
                # Ensure it's a dict or list (valid tool args)
                if isinstance(parsed_json, (dict, list)):
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {
                            'name': name,
                            'arguments': args_clean
                        }
                    })
            except json.JSONDecodeError:
                # If it looks like a tool call but isn't valid JSON, skip it
                # This prevents crashes on malformed content
                continue
                    
        return tool_calls if tool_calls else None
    
    def get_chat_id(self) -> str:
        """Get chat ID"""
        return self.chat_id
    
    def get_response_id(self) -> str:
        """Get response ID"""
        return self.response_id
