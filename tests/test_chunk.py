#!/usr/bin/env python3
from qq_agent_mcp.tools import _chunk_message

# Test 1: sentence-enders kept, grouped together (short enough)
print('=== Test 1: short sentences grouped ===')
for c in _chunk_message('你好。你呢？'):
    print(repr(c))

# Test 2: longer sentences split at sentence boundaries
print('\n=== Test 2: sentence split ===')
for c in _chunk_message('今天天气真好。我们去公园玩吧！你觉得怎么样？'):
    print(repr(c))

# Test 3: single long sentence -> clause split
print('\n=== Test 3: long sentence -> clause split ===')
for c in _chunk_message('首先，我们需要准备材料，然后开始制作，最后进行测试'):
    print(repr(c))

# Test 4: dashes in long sentence
print('\n=== Test 4: dashes in long sentence ===')
for c in _chunk_message('这个功能——也就是消息分段——非常重要，我们必须实现它'):
    print(repr(c))

# Test 5: short paragraph kept whole
print('\n=== Test 5: short kept whole ===')
for c in _chunk_message('你好呀'):
    print(repr(c))

# Test 6: mixed paragraphs
print('\n=== Test 6: mixed ===')
for c in _chunk_message('短消息\n\n这是一个比较长的段落，包含逗号、冒号：以及句号。还有问号？对吧！'):
    print(repr(c))

# Test 7: sentences that can be grouped
print('\n=== Test 7: grouping ===')
for c in _chunk_message('好的。嗯。我知道了。那我们开始吧！准备好了吗？'):
    print(repr(c))

# Test 8: all short sentences -> should group into one or two
print('\n=== Test 8: all short -> group ===')
for c in _chunk_message('好。行。嗯。对。是。'):
    print(repr(c))
