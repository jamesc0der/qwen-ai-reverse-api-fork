"""Qwen AI Stream Handler - Based on Chat2API logic"""

import json
import time
import re
from typing import Optional, Callable, Dict, Any, Generator
from http.client import IncompleteRead


class QwenAiStreamHandler:
    """Qwen AI Stream Handler"""
    
    def __init__(self, model: str, on_end: Optional[Callable[[str], None]] = None):
        """Initialize stream handler
        
        Args:
            model: Model name
            on_end: Callback function when stream ends
        """
        self.chat_id = ''
        self.model = model
        self.created = int(time.time())
        self.on_end = on_end
        self.response_id = ''
        self.content = ''
        self.tool_calls_sent = False
    
    def set_chat_id(self, chat_id: str):
        """Set chat ID"""
        self.chat_id = chat_id
    
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
                                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(initial_chunk)}\n\n'
                            initial_chunk_sent = True
                        
                        self.content += content
                        
                        if content:
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
                                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}],
                                'created': self.created,
                            }
                            yield f'data: {json.dumps(initial_chunk)}\n\n'
                            initial_chunk_sent = True
                        
                        self.content += content
                        
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
                        # Check for tool calls
                        if self._has_tool_use(self.content):
                            for chunk in self._generate_tool_calls():
                                yield chunk
                            return
                        
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
                        
                        if self.on_end and self.chat_id:
                            self.on_end(self.chat_id)
                        return
                        
                except json.JSONDecodeError:
                    continue
                    
        except IncompleteRead:
            pass
        except Exception as e:
            print(f'[QwenAI] Stream error: {e}')
        
        # Send final chunk if stream ended unexpectedly
        if not self.tool_calls_sent:
            final_chunk = {
                'id': self.response_id or self.chat_id,
                'model': self.model,
                'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                'created': self.created,
            }
            yield f'data: {json.dumps(final_chunk)}\n\n'
            yield 'data: [DONE]\n\n'
            
            if self.on_end and self.chat_id:
                self.on_end(self.chat_id)
    
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
                            
                            if self.on_end and self.chat_id:
                                self.on_end(self.chat_id)
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
        
        if self.on_end and self.chat_id:
            self.on_end(self.chat_id)
        
        return data
    
    def _has_tool_use(self, content: str) -> bool:
        """Check if content contains tool use"""
        return '[function_calls]' in content or '<tool_use>' in content
    
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
            
            if self.on_end and self.chat_id:
                self.on_end(self.chat_id)
    
    def _parse_tool_use(self, content: str) -> Optional[list]:
        """Parse tool use from content"""
        # Simple parser for tool calls
        if '[function_calls]' in content:
            # Parse bracket format: [function_calls][call:name]{"arg": "value"}[/call][/function_calls]
            pattern = r'\[call:(\w+)\](\{[^\}]+\})\[/call\]'
            matches = re.findall(pattern, content)
            if matches:
                tool_calls = []
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
                return tool_calls
        elif '<tool_use>' in content:
            # Parse XML format: <tool_use><name>name</name><arguments>...</arguments></tool_use>
            pattern = r'<tool_use>.*?<name>([^<]+)</name>.*?<arguments>([^<]+)</arguments>.*?</tool_use>'
            matches = re.findall(pattern, content, re.DOTALL)
            if matches:
                tool_calls = []
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
                return tool_calls
        return None
    
    def get_chat_id(self) -> str:
        """Get chat ID"""
        return self.chat_id
    
    def get_response_id(self) -> str:
        """Get response ID"""
        return self.response_id
