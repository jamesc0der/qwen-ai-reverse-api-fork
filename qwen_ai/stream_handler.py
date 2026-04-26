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
        self.chat_id = ''
        self.model = model
        self.created = int(time.time())
        self.on_end = on_end
        self.response_id = ''
        self.content = ''
        self.tool_calls_sent = False
        self.auto_delete_chat = auto_delete_chat
        self.delete_chat_func = delete_chat_func
        self.tools = tools
    
    def set_chat_id(self, chat_id: str):
        self.chat_id = chat_id
    
    def _handle_completion(self, chat_id: str):
        if self.on_end:
            self.on_end(chat_id)
        if self.auto_delete_chat and self.delete_chat_func and chat_id:
            try:
                self.delete_chat_func(chat_id)
                print(f'[QwenAI] Auto-deleted chat: {chat_id}')
            except Exception as e:
                print(f'[QwenAI] Failed to auto-delete chat {chat_id}: {e}')
    
    def _parse_sse_line(self, line: str) -> Optional[str]:
        if line.startswith('data: '):
            return line[6:]
        return None

    def _strip_code_spans(self, content: str) -> str:
        """Remove backtick fenced blocks and inline code spans to avoid false tool detection."""
        content = re.sub(r'```.*?```', '', content, flags=re.DOTALL)
        content = re.sub(r'`[^`]*`', '', content)
        return content

    def _find_tool_start(self, content: str) -> int:
        clean = self._strip_code_spans(content)
        tool_names = set()
        if self.tools:
            tool_names = {t.get('function', {}).get('name', '') for t in self.tools}

        if '[function_calls]' in clean:
            if not tool_names or any(f'[call:{n}]' in clean for n in tool_names):
                return clean.find('[function_calls]')

        if '<tool_use>' in clean:
            if not tool_names or any(f'<n>{n}</n>' in clean for n in tool_names):
                return clean.find('<tool_use>')

        if tool_names:
            earliest = len(content)
            for name in tool_names:
                tag = f'[{name}]'
                idx = clean.find(tag)
                if idx != -1 and f'[/{name}]' in clean[idx:]:
                    earliest = min(earliest, idx)
            return earliest

        return len(content)

    def _emit_content_chunk(self, content: str) -> str:
        chunk = {
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
        return f'data: {json.dumps(chunk)}\n\n'

    def handle_stream(self, response) -> Generator[str, None, None]:
        reasoning_text = ''
        has_sent_reasoning = False
        summary_text = ''
        initial_chunk_sent = False
        pre_tool_text_buffer = ''
        detected_tool_call = False
        pre_tool_flushed = False
        expect_tools = self.tools is not None and len(self.tools) > 0
        stream_finished_normally = False
        
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                
                line_str = line.decode('utf-8')
                
                if not line_str.startswith('data: '):
                    continue
                    
                data_str = line_str[6:]
                
                if data_str == '[DONE]':
                    continue
                
                try:
                    data = json.loads(data_str)
                    
                    if data.get('response.created', {}).get('response_id'):
                        self.response_id = data['response.created']['response_id']
                    
                    if not data.get('choices'):
                        continue
                    
                    choice = data['choices'][0]
                    delta = choice.get('delta', {})
                    phase = delta.get('phase')
                    status = delta.get('status')
                    content = delta.get('content', '')
                    
                    # ----------------------------------------------------------------
                    # Handle think phase
                    # ----------------------------------------------------------------
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
                    
                    # ----------------------------------------------------------------
                    # Handle thinking_summary phase
                    # ----------------------------------------------------------------
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
                    
                    # ----------------------------------------------------------------
                    # Handle answer phase (and phase==None with content)
                    # ----------------------------------------------------------------
                    elif phase == 'answer' or (phase is None and content):
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

                        if not detected_tool_call:
                            if self._has_tool_use(self.content):
                                detected_tool_call = True
                                split_point = self._find_tool_start(self.content)
                                pre_tool_text = self.content[:split_point]

                                if expect_tools:
                                    if pre_tool_text:
                                        yield self._emit_content_chunk(pre_tool_text)
                                    pre_tool_text_buffer = ''
                                pre_tool_flushed = True
                            else:
                                if expect_tools:
                                    pre_tool_text_buffer += content
                                else:
                                    if content:
                                        yield self._emit_content_chunk(content)

                    # ----------------------------------------------------------------
                    # Handle finished status
                    # ----------------------------------------------------------------
                    if status == 'finished' and (phase == 'answer' or phase is None):
                        if detected_tool_call:
                            for chunk in self._generate_tool_calls():
                                yield chunk
                            stream_finished_normally = True
                            self._handle_completion(self.chat_id)
                            return

                        if expect_tools and pre_tool_text_buffer:
                            if self._has_tool_use(self.content):
                                for chunk in self._generate_tool_calls():
                                    yield chunk
                                stream_finished_normally = True
                                self._handle_completion(self.chat_id)
                                return
                            else:
                                yield self._emit_content_chunk(pre_tool_text_buffer)
                                pre_tool_text_buffer = ''

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
                        
                        stream_finished_normally = True
                        self._handle_completion(self.chat_id)
                        return
                        
                except json.JSONDecodeError:
                    continue
                    
        except IncompleteRead:
            pass
        except Exception as e:
            print(f'[QwenAI] Stream error: {e}')
        finally:
            if stream_finished_normally:
                return  # already handled cleanly, do nothing
            
            if not self.tool_calls_sent:
                if detected_tool_call:
                    try:
                        for chunk in self._generate_tool_calls():
                            if chunk is not None:
                                yield chunk
                    except Exception as e:
                        print(f'[QwenAI] Error generating tool calls: {e}')
                    return

                # Flush any buffered content (stream died before status=finished)
                if expect_tools and pre_tool_text_buffer:
                    if self._has_tool_use(self.content):
                        try:
                            for chunk in self._generate_tool_calls():
                                if chunk is not None:
                                    yield chunk
                        except Exception as e:
                            print(f'[QwenAI] Error generating tool calls: {e}')
                        return
                    else:
                        yield self._emit_content_chunk(pre_tool_text_buffer)

                # Always send final chunk + DONE if we didn't finish normally
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
        
        final_reasoning = reasoning_text or summary_text
        if final_reasoning:
            data['choices'][0]['message']['reasoning_content'] = final_reasoning
        
        content = data['choices'][0]['message']['content']
        if self._has_tool_use(content):
            tool_calls = self._parse_tool_use(content)
            if tool_calls:
                split_point = self._find_tool_start(content)
                pre_tool_text = content[:split_point]
                data['choices'][0]['message']['content'] = pre_tool_text if pre_tool_text.strip() else None
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
        self._handle_completion(self.chat_id)
        return data
    
    def _has_tool_use(self, content: str) -> bool:
        clean = self._strip_code_spans(content)
        tool_names = set()
        if self.tools:
            tool_names = {t.get('function', {}).get('name', '') for t in self.tools}

        if '[function_calls]' in clean:
            # Only count it if there's a [call:known_tool] inside
            if tool_names:
                if any(f'[call:{n}]' in clean for n in tool_names):
                    return True
            else:
                return True

        if '<tool_use>' in clean:
            if tool_names:
                if any(f'<n>{n}</n>' in clean for n in tool_names):
                    return True
            else:
                return True

        # Simplified/nested tag format — only match known tool names
        if tool_names:
            for name in tool_names:
                if f'[{name}]' in clean and f'[/{name}]' in clean:
                    return True
        
        return False

    def _generate_tool_calls(self):
        """Generate tool calls chunks. If parsed calls don't match known tools,
        yield the full content as a regular text chunk instead."""
        tool_name_list = [t.get('function', {}).get('name', '') for t in (self.tools or [])]
        all_parsed = self._parse_tool_use(self.content) or []

        tool_calls = [tc for tc in all_parsed if tc.get('function', {}).get('name', '') in tool_name_list]
        not_tool_calls = [tc for tc in all_parsed if tc.get('function', {}).get('name', '') not in tool_name_list]

        if tool_calls:
            self.tool_calls_sent = True

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

        else:
            # No matching tool calls (either not_tool_calls or completely empty parse)
            # Emit text up to where tool syntax started, suppress the rest
            split_point = self._find_tool_start(self.content)
            text_to_emit = self.content[:split_point]
            if text_to_emit.strip():
                yield self._emit_content_chunk(text_to_emit)
            finish_chunk = {
                'id': self.response_id or self.chat_id,
                'model': self.model,
                'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                'created': self.created,
            }
            yield f'data: {json.dumps(finish_chunk)}\n\n'
            yield 'data: [DONE]\n\n'
            self._handle_completion(self.chat_id)
    
    def _is_json_tool_call(self, content: str) -> bool:
        content = content.strip()
        if not content.startswith('{'):
            return False
        try:
            data = json.loads(content)
            return 'name' in data and ('arguments' in data or 'parameters' in data)
        except json.JSONDecodeError:
            return False

    def _generate_json_tool_calls(self, content: str):
        try:
            data = json.loads(content.strip())
            tool_name = data.get('name', '')
            arguments = data.get('arguments', data.get('parameters', {}))
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            
            self.tool_calls_sent = True
            
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

    def _extract_json_from_pos(self, content: str, start: int) -> Optional[str]:
        """Extract a complete JSON object or array using brace counting."""
        if start >= len(content):
            return None

        opener = content[start]
        if opener == '{':
            closer = '}'
        elif opener == '[':
            closer = ']'
        else:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i in range(start, len(content)):
            ch = content[i]

            if escape_next:
                escape_next = False
                continue

            if ch == '\\' and in_string:
                escape_next = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return content[start:i + 1]

        return None

    def _parse_tool_use(self, content: str) -> Optional[list]:
        """Parse tool use from content. Strips code spans first."""
        content = self._strip_code_spans(content)
        tool_calls = []

        # 1. Standard Format: [function_calls][call:name]{...}[/call][/function_calls]
        if '[function_calls]' in content:
            for match in re.finditer(r'\[call:(\w+)\]', content):
                name = match.group(1)
                json_start = content.find('{', match.end())
                if json_start == -1:
                    continue
                peek = content[json_start + 1:].lstrip()
                if not (peek.startswith('"') or peek.startswith('}')):
                    continue
                args_clean = self._extract_json_from_pos(content, json_start)
                if args_clean is None:
                    continue
                try:
                    json.loads(args_clean)
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {'name': name, 'arguments': args_clean}
                    })
                except json.JSONDecodeError:
                    continue
            if tool_calls:
                return tool_calls

        # 2. XML Format: <tool_use><n>name</n><arguments>...</arguments></tool_use>
        if '<tool_use>' in content:
            pattern = r'<tool_use>.*?<n>([^<]+)</n>.*?<arguments>(.*?)</arguments>.*?</tool_use>'
            for name, args_str in re.findall(pattern, content, re.DOTALL):
                args_clean = args_str.strip()
                try:
                    json.loads(args_clean)
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {'name': name.strip(), 'arguments': args_clean}
                    })
                except json.JSONDecodeError:
                    continue
            if tool_calls:
                return tool_calls

        # 3. Simplified / Nested-tag Format
        SKIP_NAMES = {'function_calls', 'call'}

        for match in re.finditer(r'\[(\w+)\]', content):
            name = match.group(1)
            if name.lower() in SKIP_NAMES:
                continue

            closing_tag = f'[/{name}]'
            # Also accept mismatched closing like [/todo] for [todo_write]
            has_exact_close = closing_tag in content[match.end():]
            has_any_close = bool(re.search(r'\[/\w+\]', content[match.end():]))
            if not has_exact_close and not has_any_close:
                continue

            json_start = match.end()
            while json_start < len(content) and content[json_start] in ' \t\r\n':
                json_start += 1

            if json_start >= len(content):
                continue

            next_char = content[json_start]

            if next_char == '{':
                # Sub-format a: JSON object inline [tool_name]{...}[/tool_name]
                peek = content[json_start + 1:].lstrip()
                if not (peek.startswith('"') or peek.startswith('}')):
                    continue
                args_clean = self._extract_json_from_pos(content, json_start)
                if args_clean is None:
                    continue
                try:
                    parsed_json = json.loads(args_clean)
                    if isinstance(parsed_json, (dict, list)):
                        tool_calls.append({
                            'id': f'tool_{len(tool_calls)}',
                            'function': {'name': name, 'arguments': args_clean}
                        })
                except json.JSONDecodeError:
                    continue

            elif next_char == '[':
                # Sub-format b: nested sub-tags [tool_name][param]val[/param][/tool_name]
                close_pos = content.find(closing_tag, match.end())
                if close_pos == -1:
                    continue
                inner = content[match.end():close_pos]
                if not inner.lstrip().startswith('['):
                    continue

                params: dict = {}
                for sub in re.finditer(r'\[(\w+)\](.*?)\[/\1\]', inner, re.DOTALL):
                    sub_name = sub.group(1)
                    sub_val = sub.group(2).strip()
                    try:
                        params[sub_name] = json.loads(sub_val)
                    except (json.JSONDecodeError, ValueError):
                        params[sub_name] = sub_val

                if params:
                    tool_calls.append({
                        'id': f'tool_{len(tool_calls)}',
                        'function': {'name': name, 'arguments': json.dumps(params)}
                    })

        return tool_calls if tool_calls else None

    def get_chat_id(self) -> str:
        return self.chat_id
    
    def get_response_id(self) -> str:
        return self.response_id