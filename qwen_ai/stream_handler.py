"""Qwen AI Stream Handler - Based on Chat2API logic"""

import json
import time
import re
from typing import Optional, Callable, Dict, Any, Generator
from http.client import IncompleteRead

from .debug_logger import log_raw, log_exception, log_tool_detected, log_tool_parsed, log_stream_chunk


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

        # Find the start of any tool syntax
        if '<function_calls>' in clean:
            return clean.find('<function_calls>')
        if '<function_call>' in clean:
            return clean.find('<function_call>')
        if '<tool_use>' in clean:
            return clean.find('<tool_use>')

        # Find tool name tags
        if tool_names:
            earliest = len(content)
            for name in tool_names:
                tag = f'<{name}>'
                idx = clean.find(tag)
                if idx != -1:
                    earliest = min(earliest, idx)
            if earliest < len(content):
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
    
    def _strip_injected_history(self, content: str) -> str:
        """Remove tool results and conversation history that Qwen echoes back in its response."""
        # Remove Tool Result blocks: "Tool Result [id]: ...text..." up to next blank line or tool call
        # content = re.sub(r'Tool Result \<[^\>]*\>:.*?(?=\n\n|\<function_calls\>|\<function_call\>|$)', 
        #                 '', content, flags=re.DOTALL)
        
        content = re.sub(r'\<tool_result id=\".*\"\>.*\<\/tool_result\>', 
                        '', content, flags=re.DOTALL)
        
        # Remove "User: ..." and "Assistant: ..." prefixes that get echoed
        content = re.sub(r'\n(User|Assistant):\s', '\n', content)
        
        # Clean up excessive blank lines left behind
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        return content

    def handle_stream(self, response) -> Generator[str, None, None]:
        log_raw("DEBUG", "STREAM_HANDLER", f"Starting stream handling for chat_id={self.chat_id}, model={self.model}")
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
                    log_raw("DEBUG", "STREAM_HANDLER", "[DONE] marker received")
                    continue
                
                try:
                    data = json.loads(data_str)
                    log_stream_chunk(0, data.get('choices', [{}])[0].get('delta', {}).get('phase'), data_str[:200], None)

                    if data.get('response.created', {}).get('response_id'):
                        self.response_id = data['response.created']['response_id']
                        log_raw("DEBUG", "STREAM_HANDLER", f"Response ID set: {self.response_id}")
                    
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

                        # Strip echoed history before processing
                        self.content = self._strip_injected_history(self.content)

                        if not detected_tool_call:
                            if self._has_tool_use(self.content):
                                log_tool_detected(0, self.content)
                                detected_tool_call = True
                                split_point = self._find_tool_start(self.content)
                                pre_tool_text = self.content[:split_point]

                                if expect_tools:
                                    if pre_tool_text:
                                        yield self._emit_content_chunk(pre_tool_text)
                                    pre_tool_text_buffer = ''
                                pre_tool_flushed = True
                            elif self._has_partial_tool_syntax(self.content):
                                # Buffer content when partial tool syntax is detected
                                if expect_tools:
                                    pre_tool_text_buffer += content
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
                            log_raw("DEBUG", "STREAM_HANDLER", "Generating tool call chunks")
                            for chunk in self._generate_tool_calls():
                                yield chunk
                            stream_finished_normally = True
                            self._handle_completion(self.chat_id)
                            log_raw("DEBUG", "STREAM_HANDLER", "Stream finished normally with tool calls")
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
                        log_raw("DEBUG", "STREAM_HANDLER", f"Emitting final chunk with finish_reason={finish_reason}")
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
                        log_raw("DEBUG", "STREAM_HANDLER", "Stream finished normally")
                        return
                        
                except json.JSONDecodeError:
                    continue
                    
        except IncompleteRead:
            log_raw("WARNING", "STREAM_HANDLER", "IncompleteRead exception caught")
            pass
        except Exception as e:
            log_exception("stream_handler.handle_stream", e)
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
                        log_exception("stream_handler.generate_tool_calls_finally", e)
                    return

                # Flush any buffered content (stream died before status=finished)
                if expect_tools and pre_tool_text_buffer:
                    if self._has_tool_use(self.content):
                        try:
                            for chunk in self._generate_tool_calls():
                                if chunk is not None:
                                    yield chunk
                        except Exception as e:
                            log_exception("stream_handler.generate_tool_calls_finally_2", e)
                        return
                    else:
                        yield self._emit_content_chunk(pre_tool_text_buffer)

                # Always send final chunk + DONE if we didn't finish normally
                log_raw("WARNING", "STREAM_HANDLER", "Stream did not finish normally, emitting fallback chunks")
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
        log_raw("DEBUG", "STREAM_HANDLER", f"Starting non-stream handling for chat_id={self.chat_id}, model={self.model}")
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
                        log_raw("DEBUG", "STREAM_HANDLER", f"Non-stream response ID set: {self.response_id}")
                    
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
            log_exception("stream_handler.handle_non_stream", e)
        
        final_reasoning = reasoning_text or summary_text
        if final_reasoning:
            data['choices'][0]['message']['reasoning_content'] = final_reasoning
        
        content = data['choices'][0]['message']['content']
        if self._has_tool_use(content):
            log_tool_detected(0, content)
            tool_calls = self._parse_tool_use(content)
            if tool_calls:
                log_tool_parsed(0, tool_calls)
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
        log_raw("DEBUG", "STREAM_HANDLER", "Non-stream handling completed")
        return data
    
    def _has_partial_tool_syntax(self, content: str) -> bool:
        """Check for partial tool call syntax that should be buffered."""
        clean = self._strip_code_spans(content)

        # Check for any XML-like tags that could be tool syntax
        if '<' in clean:
            return True

        return False

    def _has_tool_use(self, content: str) -> bool:
        clean = self._strip_code_spans(content)

        if '<function_calls>' in clean or '<function_call>' in clean:
            return True

        if '<tool_use>' in clean:
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
        if re.search(attr_pattern, clean):
            return True

        return False

    def _generate_tool_calls(self):
        """Generate tool calls chunks for all parsed tool calls."""
        log_raw("DEBUG", "STREAM_HANDLER", f"Generating tool calls, known tools: {[t.get('function', {}).get('name', '') for t in (self.tools or [])]}")
        all_parsed = self._parse_tool_use(self.content) or []
        if all_parsed:
            log_tool_parsed(0, all_parsed)

        if all_parsed:
            self.tool_calls_sent = True
            # Strip tool syntax from content when emitting tool calls
            split_point = self._find_tool_start(self.content)
            self.content = self.content[:split_point]

            for i, tc in enumerate(all_parsed):
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
        elif opener == '<':
            closer = '>'
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
        # content = self._strip_code_spans(content)
        tool_calls = []

        # 1. Standard Format: <function_calls><call:name>{...}</call></function_calls>
        if '<function_calls>' in content or '<function_call>' in content:
            for match in re.finditer(r'\<call:(\w+)\>', content):
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
                        'id': f'call_{int(time.time() * 1000)}_{len(tool_calls)}',
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
                        'id': f'call_{int(time.time() * 1000)}_{len(tool_calls)}',
                        'function': {'name': name.strip(), 'arguments': args_clean}
                    })
                except json.JSONDecodeError:
                    continue
            if tool_calls:
                return tool_calls

        # 3. Simplified / Nested-tag Format
        SKIP_NAMES = {'function_calls', 'function_call', 'call'}

        for match in re.finditer(r'\<(\w+)\>', content):
            name = match.group(1)
            if name.lower() in SKIP_NAMES:
                continue

            closing_tag = f'</{name}>'
            # Also accept mismatched closing like </todo> for <todo_write>
            has_exact_close = closing_tag in content[match.end():]
            has_any_close = bool(re.search(r'\</\w+\>', content[match.end():]))
            if not has_exact_close and not has_any_close:
                continue

            json_start = match.end()
            while json_start < len(content) and content[json_start] in ' \t\r\n':
                json_start += 1

            if json_start >= len(content):
                continue

            next_char = content[json_start]

            if next_char == '{':
                # Sub-format a: JSON object inline <tool_name>{...}</tool_name>
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
                            'id': f'call_{int(time.time() * 1000)}_{len(tool_calls)}',
                            'function': {'name': name, 'arguments': args_clean}
                        })
                except json.JSONDecodeError:
                    continue

            elif next_char == '<':
                # Sub-format b: nested sub-tags <tool_name><param>val</param></tool_name>
                close_pos = content.find(closing_tag, match.end())
                if close_pos == -1:
                    continue
                inner = content[match.end():close_pos]
                if not inner.lstrip().startswith('<'):
                    continue

                params: dict = {}
                pos = 0
                while pos < len(inner):
                    # Find next opening tag <name>
                    open_match = re.search(r'\<(\w+)\>', inner[pos:])
                    if not open_match:
                        break
                    sub_name = open_match.group(1)
                    content_start = pos + open_match.end()
                    
                    # Find the LAST occurrence of </name> (not first) to handle nested content
                    close_tag = f'</{sub_name}>'
                    # Search from content_start forward
                    close_pos = inner.find(close_tag, content_start)
                    if close_pos == -1:
                        break
                    
                    raw_val = inner[content_start:close_pos]
                    # Trim only single wrapping newline from tag formatting
                    if raw_val.startswith('\n'):
                        raw_val = raw_val[1:]
                    if raw_val.endswith('\n'):
                        raw_val = raw_val[:-1]
                    
                    try:
                        params[sub_name] = json.loads(raw_val)
                    except (json.JSONDecodeError, ValueError):
                        params[sub_name] = raw_val  # exact string, no strip
                    
                    pos = close_pos + len(close_tag)

                if params:
                    tool_calls.append({
                        'id': f'call_{int(time.time() * 1000)}_{len(tool_calls)}',
                        'function': {'name': name, 'arguments': json.dumps(params)}
                    })

        # 4. XML Attribute Format: <tag_name attr="value"></tag_name>
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
                        'id': f'call_{int(time.time() * 1000)}_{len(tool_calls)}',
                        'function': {'name': name, 'arguments': json.dumps(attr_dict)}
                    })

        seen = set()
        unique_calls = []
        for tc in tool_calls:
            # Normalize arguments for dedup comparison
            try:
                args_normalized = json.dumps(json.loads(tc['function']['arguments']), sort_keys=True)
            except (json.JSONDecodeError, ValueError):
                args_normalized = tc['function']['arguments']
            key = (tc['function']['name'], args_normalized)
            if key not in seen:
                seen.add(key)
                # Reassign sequential IDs AFTER dedup to ensure uniqueness
                tc['id'] = f'call_{int(time.time() * 1000)}_{len(unique_calls)}'
                unique_calls.append(tc)

        return unique_calls if unique_calls else None

    def get_chat_id(self) -> str:
        return self.chat_id

    def get_response_id(self) -> str:
        return self.response_id