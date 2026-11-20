import React from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/index.css';
import MessageItem from '../../src/components/chat/message/MessageItem';
import { ChatAttachmentProvider } from '../../src/components/chat/attachments/ChatAttachmentProvider';
import { useChatAttachmentContext } from '../../src/components/chat/attachments/ChatAttachmentContext';
import { createWSMessageHandlers } from '../../src/contexts/wsMessageHandlers';
import { useMessageStore } from '../../src/stores/useMessageStore';

const ref = (current) => ({ current });
const subscriptions = [];
const handlers = createWSMessageHandlers({
  getStore: () => useMessageStore.getState(),
  subscribedTasksRef: ref(new Set()),
  taskConversationMapRef: ref(new Map()),
  operationContextRef: ref(new Map()),
  chunkBufferRef: ref(new Map()),
  flushTimerRef: ref(null),
  deliveryCursorRef: ref(new Map()),
  unsubscribeTask: () => {},
  send: (event) => subscriptions.push(event),
});
window.chatImageReplay = {
  apply(event) {
    handlers[event.type](event);
  },
  subscriptions,
};

function App() {
  const messages = useMessageStore((state) => state.messages);
  const { submissionSnapshot } = useChatAttachmentContext();
  const [submission, setSubmission] = React.useState([]);
  return (
    <main className="mx-auto max-w-3xl p-6">
      {Object.values(messages).flat().map((message) => (
        <article key={message.id} data-status={message.status}>
          <MessageItem message={message} />
        </article>
      ))}
      <button onClick={() => setSubmission(submissionSnapshot.imageInputs)}>
        读取引用后的提交参数
      </button>
      <output>{JSON.stringify(submission)}</output>
    </main>
  );
}

createRoot(document.getElementById('root')).render(
  <ChatAttachmentProvider><App /></ChatAttachmentProvider>,
);
