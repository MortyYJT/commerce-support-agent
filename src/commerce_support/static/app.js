(function () {
  "use strict";

  const form = document.getElementById("chatForm");
  const input = document.getElementById("messageInput");
  const sendButton = document.getElementById("sendButton");
  const sendIcon = document.getElementById("sendIcon");
  const stopIcon = document.getElementById("stopIcon");
  const sendLabel = document.getElementById("sendLabel");
  const newChatButton = document.getElementById("newChatButton");
  const messageList = document.getElementById("messageList");
  const emptyState = document.getElementById("emptyState");
  const announcer = document.getElementById("announcer");

  const completedHistory = [];
  const maxHistoryMessages = 40;
  let activeRequest = null;
  let keepScrolledToBottom = true;
  const characterDelayMs = 17;

  class PublicServiceError extends Error {
    constructor(message) {
      super(message);
      this.name = "PublicServiceError";
      this.publicMessage = message;
    }
  }

  class SSEParser {
    constructor(onEvent) {
      this.onEvent = onEvent;
      this.buffer = "";
      this.dataLines = [];
      this.eventName = "";
    }

    push(chunk) {
      this.buffer += chunk;
      while (this.buffer.length > 0) {
        let separatorIndex = -1;
        for (let index = 0; index < this.buffer.length; index += 1) {
          const character = this.buffer[index];
          if (character === "\n" || character === "\r") {
            separatorIndex = index;
            break;
          }
        }

        if (separatorIndex === -1) {
          return;
        }

        const separator = this.buffer[separatorIndex];
        if (separator === "\r" && separatorIndex === this.buffer.length - 1) {
          return;
        }

        const line = this.buffer.slice(0, separatorIndex);
        let separatorLength = 1;
        if (separator === "\r" && this.buffer[separatorIndex + 1] === "\n") {
          separatorLength = 2;
        }
        this.buffer = this.buffer.slice(separatorIndex + separatorLength);
        this.processLine(line);
      }
    }

    finish() {
      if (!this.buffer) {
        return;
      }

      let line = this.buffer;
      this.buffer = "";
      if (line.endsWith("\r")) {
        line = line.slice(0, -1);
        this.processLine(line);
        return;
      }
      this.processLine(line);
    }

    processLine(line) {
      if (line === "") {
        if (this.dataLines.length > 0) {
          this.onEvent({
            type: this.eventName || "message",
            data: this.dataLines.join("\n")
          });
        }
        this.dataLines = [];
        this.eventName = "";
        return;
      }

      if (line.startsWith(":")) {
        return;
      }

      const colonIndex = line.indexOf(":");
      const field = colonIndex === -1 ? line : line.slice(0, colonIndex);
      let value = colonIndex === -1 ? "" : line.slice(colonIndex + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }

      if (field === "data") {
        this.dataLines.push(value);
      } else if (field === "event") {
        this.eventName = value;
      }
    }
  }

  function announce(message) {
    announcer.textContent = "";
    window.setTimeout(function () {
      announcer.textContent = message;
    }, 15);
  }

  function isCurrentRequest(request) {
    return activeRequest === request && !request.cancelled;
  }

  function isNearBottom() {
    const distance = messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
    return distance < 110;
  }

  function scrollToBottom() {
    messageList.scrollTop = messageList.scrollHeight;
    keepScrolledToBottom = true;
  }

  function scrollIfPinned() {
    if (keepScrolledToBottom || isNearBottom()) {
      scrollToBottom();
    }
  }

  messageList.addEventListener("scroll", function () {
    keepScrolledToBottom = isNearBottom();
  });

  function createFishIcon() {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.setAttribute("class", "assistant-fish-icon");
    svg.setAttribute("viewBox", "0 0 48 48");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");

    const fishShapes = [
      {
        tag: "path",
        attributes: {
          d: "M14 24c4.1-7.1 10.4-10.3 17.2-9.4 6 .8 10.4 4.1 12.8 9.4-2.4 5.3-6.8 8.6-12.8 9.4C24.4 34.3 18.1 31.1 14 24Z",
          fill: "none"
        }
      },
      {
        tag: "path",
        attributes: {
          d: "m14 24-8-7v14l8-7Z",
          fill: "none"
        }
      },
      {
        tag: "path",
        attributes: {
          d: "M25.5 16.3c-2 4.4-2 11 0 15.4",
          fill: "none"
        }
      },
      {
        tag: "circle",
        attributes: {
          cx: "35.5",
          cy: "21.3",
          r: "1.2",
          fill: "currentColor"
        }
      }
    ];

    fishShapes.forEach(function (shape) {
      const node = document.createElementNS(namespace, shape.tag);
      Object.entries(shape.attributes).forEach(function (entry) {
        node.setAttribute(entry[0], entry[1]);
      });
      if (shape.tag === "path") {
        node.setAttribute("stroke", "currentColor");
        node.setAttribute("stroke-width", "2.1");
        node.setAttribute("stroke-linecap", "round");
        node.setAttribute("stroke-linejoin", "round");
      }
      svg.appendChild(node);
    });
    return svg;
  }

  function createMessage(role, text) {
    const article = document.createElement("article");
    article.className = "message message-" + role;
    article.dataset.state = role === "assistant" ? "thinking" : "complete";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    if (role === "assistant") {
      avatar.classList.add("assistant-fish-avatar");
      avatar.appendChild(createFishIcon());
    } else {
      avatar.textContent = "我";
    }

    const main = document.createElement("div");
    main.className = "message-main";

    const label = document.createElement("div");
    label.className = "message-label";
    const author = document.createElement("span");
    author.textContent = role === "assistant" ? "谷鱼Y的客服助手" : "你";
    label.appendChild(author);

    const status = document.createElement("span");
    status.className = "message-status";
    if (role === "assistant") {
      status.textContent = "正在等待回复";
      label.appendChild(status);
    }

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const body = document.createElement("p");
    body.className = "message-text";
    body.textContent = text || "";
    bubble.appendChild(body);

    const pending = document.createElement("div");
    pending.className = "message-pending";
    const dots = document.createElement("span");
    dots.className = "pending-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) {
      dots.appendChild(document.createElement("i"));
    }
    const pendingText = document.createElement("span");
    pendingText.textContent = "正在等待回复…";
    pending.appendChild(dots);
    pending.appendChild(pendingText);

    const note = document.createElement("p");
    note.className = "message-note";
    note.hidden = true;

    main.appendChild(label);
    main.appendChild(bubble);
    if (role === "assistant") {
      main.appendChild(pending);
      main.appendChild(note);
    }

    if (role === "assistant") {
      article.appendChild(avatar);
      article.appendChild(main);
    } else {
      article.appendChild(main);
      article.appendChild(avatar);
      body.textContent = text;
    }

    messageList.appendChild(article);
    emptyState.hidden = true;
    scrollIfPinned();

    return {
      root: article,
      body: body,
      status: status,
      pending: pending,
      note: note
    };
  }

  function updateComposer() {
    const generating = Boolean(activeRequest);
    if (generating) {
      sendButton.classList.add("is-stop");
      sendButton.disabled = false;
      sendButton.setAttribute("aria-label", "停止生成");
      sendButton.title = "停止生成";
      sendIcon.toggleAttribute("hidden", true);
      stopIcon.toggleAttribute("hidden", false);
      sendLabel.textContent = "";
      sendLabel.toggleAttribute("hidden", true);
    } else {
      sendButton.classList.remove("is-stop");
      sendButton.disabled = input.value.trim().length === 0;
      sendButton.setAttribute("aria-label", "发送消息");
      sendButton.title = "发送消息";
      sendIcon.toggleAttribute("hidden", false);
      stopIcon.toggleAttribute("hidden", true);
      sendLabel.textContent = "发送";
      sendLabel.toggleAttribute("hidden", false);
    }
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 132) + "px";
  }

  function setAssistantStatus(message, state) {
    const request = activeRequest;
    if (!request) {
      return;
    }
    request.assistant.root.dataset.state = state;
    request.assistant.status.textContent = message;
  }

  function resolveTypeWaiters(request) {
    const waiters = request.typeWaiters.splice(0);
    waiters.forEach(function (resolve) {
      resolve();
    });
  }

  function discardQueuedText(request) {
    if (request.typeTimer !== null) {
      window.clearTimeout(request.typeTimer);
      request.typeTimer = null;
    }
    request.characterQueue.length = 0;
    resolveTypeWaiters(request);
  }

  function pumpCharacters(request) {
    if (!isCurrentRequest(request)) {
      discardQueuedText(request);
      return;
    }

    if (request.characterQueue.length === 0) {
      request.typeTimer = null;
      resolveTypeWaiters(request);
      return;
    }

    const character = request.characterQueue.shift();
    request.displayedText += character;
    request.assistant.body.textContent = request.displayedText;
    if (!request.hasVisibleText) {
      request.hasVisibleText = true;
      request.assistant.body.classList.add("is-streaming");
      request.assistant.pending.hidden = true;
      request.assistant.root.dataset.state = "streaming";
      request.assistant.status.textContent = "正在回复";
    }
    scrollIfPinned();

    request.typeTimer = window.setTimeout(function () {
      request.typeTimer = null;
      pumpCharacters(request);
    }, characterDelayMs);
  }

  function enqueueCharacters(request, text) {
    for (const character of text) {
      request.characterQueue.push(character);
    }
    if (request.typeTimer === null && request.characterQueue.length > 0) {
      pumpCharacters(request);
    }
  }

  function waitForTyping(request) {
    if (request.typeTimer === null && request.characterQueue.length === 0) {
      return Promise.resolve();
    }
    return new Promise(function (resolve) {
      request.typeWaiters.push(resolve);
    });
  }

  function markRequestFailed(request, detail) {
    if (!isCurrentRequest(request)) {
      return;
    }

    request.assistant.pending.hidden = true;
    request.assistant.body.classList.remove("is-streaming");
    if (!request.displayedText) {
      request.assistant.body.textContent = "这次暂时没有完成回复，请稍后重试。";
    }
    request.assistant.root.dataset.state = "failed";
    request.assistant.status.textContent = "回复未完成";
    const explanation = detail ? "服务提示：" + detail : "请稍后重试";
    request.assistant.note.textContent = explanation + "。本轮未加入后续对话上下文。";
    request.assistant.note.hidden = false;
    announce("回复未完成，本轮未加入后续对话上下文。");
    activeRequest = null;
    updateComposer();
  }

  function messageFromPayload(payload) {
    if (typeof payload === "string") {
      return payload.trim();
    }
    if (!payload || typeof payload !== "object") {
      return "";
    }
    if (typeof payload.message === "string" && payload.message.trim()) {
      return payload.message.trim();
    }
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
    if (typeof payload.error === "string" && payload.error.trim()) {
      return payload.error.trim();
    }
    if (payload.error && typeof payload.error === "object") {
      if (typeof payload.error.message === "string" && payload.error.message.trim()) {
        return payload.error.message.trim();
      }
    }
    return "";
  }

  function conciseErrorMessage(value) {
    if (!value) {
      return "";
    }
    return Array.from(value).slice(0, 180).join("");
  }

  async function readHttpError(response) {
    let raw = "";
    try {
      raw = await response.text();
    } catch (_error) {
      return "";
    }
    if (!raw) {
      return "";
    }

    try {
      return conciseErrorMessage(messageFromPayload(JSON.parse(raw)));
    } catch (_error) {
      return "";
    }
  }

  function readEventPayload(event) {
    try {
      return JSON.parse(event.data);
    } catch (_error) {
      return null;
    }
  }

  function handleSSEEvent(event, request) {
    if (!isCurrentRequest(request)) {
      return;
    }

    if (event.type === "delta") {
      if (request.doneSeen) {
        request.protocolError = "服务响应顺序异常";
        return;
      }
      const payload = readEventPayload(event);
      if (!payload || typeof payload.content !== "string") {
        request.protocolError = "服务响应格式异常";
        return;
      }
      if (payload.content.length > 0) {
        request.answerText += payload.content;
        enqueueCharacters(request, payload.content);
      }
      return;
    }

    if (event.type === "done") {
      if (request.doneSeen) {
        request.protocolError = "服务响应顺序异常";
        return;
      }
      const payload = readEventPayload(event);
      if (!payload || payload.finish_reason !== "stop") {
        request.protocolError = "回复没有正常结束";
        return;
      }
      request.doneSeen = true;
      return;
    }

    if (event.type === "error") {
      const payload = readEventPayload(event);
      const message = payload ? conciseErrorMessage(messageFromPayload(payload)) : "";
      request.serverError = message || "服务暂时无法完成这次回复。";
    }
  }

  async function consumeStream(response, request) {
    if (!response.body || typeof response.body.getReader !== "function") {
      throw new PublicServiceError("浏览器无法读取服务回复，请刷新页面后重试。");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const parser = new SSEParser(function (event) {
      handleSSEEvent(event, request);
    });
    let cancelReader = false;

    try {
      while (isCurrentRequest(request)) {
        const result = await reader.read();
        if (result.done) {
          parser.push(decoder.decode());
          parser.finish();
          break;
        }

        parser.push(decoder.decode(result.value, { stream: true }));
        if (request.serverError || request.protocolError) {
          cancelReader = true;
          break;
        }
      }
    } finally {
      if (cancelReader) {
        try {
          await reader.cancel();
        } catch (_error) {
          // The request may already have been aborted by the user.
        }
      }
      try {
        reader.releaseLock();
      } catch (_error) {
        // The stream can already be unlocked after cancellation.
      }
    }
  }

  async function sendMessage() {
    if (activeRequest) {
      return;
    }

    const message = input.value;
    if (!message.trim()) {
      updateComposer();
      return;
    }

    input.value = "";
    resizeInput();

    const userMessage = createMessage("user", message);
    const assistantMessage = createMessage("assistant", "");
    keepScrolledToBottom = true;
    scrollToBottom();
    const controller = new AbortController();
    const request = {
      controller: controller,
      userMessage: message,
      assistant: assistantMessage,
      history: completedHistory.map(function (entry) {
        return { role: entry.role, content: entry.content };
      }),
      answerText: "",
      displayedText: "",
      characterQueue: [],
      typeTimer: null,
      typeWaiters: [],
      hasVisibleText: false,
      doneSeen: false,
      protocolError: "",
      serverError: "",
      cancelled: false
    };

    activeRequest = request;
    userMessage.root.dataset.state = "complete";
    assistantMessage.status.textContent = "正在等待回复";
    assistantMessage.pending.hidden = false;
    updateComposer();
    announce("消息已发送，正在等待回复。");

    try {
      const response = await fetch("/chat/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream"
        },
        body: JSON.stringify({
          message: message,
          history: request.history
        }),
        signal: controller.signal
      });

      if (!isCurrentRequest(request)) {
        return;
      }

      if (!response.ok) {
        const publicMessage = await readHttpError(response);
        throw new PublicServiceError(publicMessage || "请求暂时无法完成，请稍后重试。");
      }

      await consumeStream(response, request);
      if (!isCurrentRequest(request)) {
        return;
      }

      if (request.serverError || request.protocolError || !request.doneSeen || !request.answerText) {
        await waitForTyping(request);
        if (!isCurrentRequest(request)) {
          return;
        }
        const reason = request.serverError || request.protocolError ||
          (!request.doneSeen ? "服务连接提前结束，请重试。" : "没有收到可显示的回复内容。");
        markRequestFailed(request, reason);
        return;
      }

      await waitForTyping(request);
      if (!isCurrentRequest(request)) {
        return;
      }

      request.assistant.body.classList.remove("is-streaming");
      request.assistant.root.dataset.state = "complete";
      request.assistant.status.textContent = "回复完成";
      request.assistant.note.hidden = true;
      completedHistory.push(
        { role: "user", content: request.userMessage },
        { role: "assistant", content: request.answerText }
      );
      if (completedHistory.length > maxHistoryMessages) {
        completedHistory.splice(0, completedHistory.length - maxHistoryMessages);
      }
      activeRequest = null;
      updateComposer();
      announce("谷鱼Y的客服助手已回复，可以继续追问。");
    } catch (error) {
      if (!isCurrentRequest(request)) {
        return;
      }
      await waitForTyping(request);
      if (!isCurrentRequest(request)) {
        return;
      }
      const message = error instanceof PublicServiceError
        ? error.publicMessage
        : "连接暂时中断，请检查网络后重试。";
      markRequestFailed(request, message);
    } finally {
      if (isCurrentRequest(request)) {
        activeRequest = null;
        updateComposer();
      }
    }
  }

  function stopGeneration() {
    const request = activeRequest;
    if (!request) {
      return;
    }

    activeRequest = null;
    request.cancelled = true;
    request.controller.abort();
    discardQueuedText(request);
    request.assistant.pending.hidden = true;
    request.assistant.body.classList.remove("is-streaming");
    if (!request.displayedText) {
      request.assistant.body.textContent = "回复已停止。";
    }
    request.assistant.root.dataset.state = "stopped";
    request.assistant.status.textContent = "已停止";
    request.assistant.note.textContent = "本轮未加入后续对话上下文。";
    request.assistant.note.hidden = false;
    updateComposer();
    announce("回复已停止，本轮未加入后续对话上下文。");
  }

  function cancelForNewConversation() {
    const request = activeRequest;
    if (!request) {
      return;
    }
    activeRequest = null;
    request.cancelled = true;
    request.controller.abort();
    discardQueuedText(request);
  }

  function startNewConversation() {
    cancelForNewConversation();
    completedHistory.length = 0;
    messageList.querySelectorAll(".message").forEach(function (message) {
      message.remove();
    });
    emptyState.hidden = false;
    input.value = "";
    resizeInput();
    updateComposer();
    scrollToBottom();
    announce("已开始新对话。");
    input.focus();
  }

  input.addEventListener("input", function () {
    resizeInput();
    updateComposer();
  });

  input.addEventListener("keydown", function (event) {
    const isComposing = event.isComposing || event.keyCode === 229;
    if (event.key === "Enter" && !event.shiftKey && !isComposing) {
      event.preventDefault();
      if (!activeRequest) {
        void sendMessage();
      }
    }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!activeRequest) {
      void sendMessage();
    }
  });

  sendButton.addEventListener("click", function (event) {
    if (activeRequest) {
      event.preventDefault();
      stopGeneration();
    }
  });

  newChatButton.addEventListener("click", startNewConversation);

  document.querySelectorAll("[data-suggestion]").forEach(function (button) {
    button.addEventListener("click", function () {
      input.value = button.getAttribute("data-suggestion") || "";
      resizeInput();
      updateComposer();
      input.focus();
    });
  });

  resizeInput();
  updateComposer();
})();
