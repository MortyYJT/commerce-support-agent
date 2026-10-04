from commerce_support import context, prompts


def test_user_braces_are_literal_and_system_prompt_is_unchanged():
    system = prompts.render_system_prompt()
    current_message = "请查询订单 {order_id} 的物流"

    messages = context.build_chat_messages(
        system=system,
        history=[],
        message=current_message,
        budget=4096,
    )

    assert messages[0].content == system
    assert messages[-1].content == current_message
