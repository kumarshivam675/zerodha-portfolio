import { Suspense } from "react";
import Chat from "@/components/Chat";

export default function AgentPage() {
  return (
    <Suspense>
      <Chat />
    </Suspense>
  );
}
