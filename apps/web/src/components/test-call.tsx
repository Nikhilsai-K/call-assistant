"use client";

import {
  LiveKitRoom,
  RoomAudioRenderer,
  StartAudio,
  useLocalParticipant,
} from "@livekit/components-react";

function MicToggle() {
  const { localParticipant } = useLocalParticipant();
  return (
    <button
      className="rounded-md border px-3 py-2 text-sm"
      onClick={async () => {
        await localParticipant.setMicrophoneEnabled(
          !localParticipant.isMicrophoneEnabled,
        );
      }}
    >
      {localParticipant?.isMicrophoneEnabled ? "Mute" : "Unmute"}
    </button>
  );
}

export function TestCall({
  livekitUrl,
  token,
  room,
}: {
  livekitUrl: string;
  token: string;
  room: string;
}) {
  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">Test call · room {room}</div>
        <StartAudio label="Enable audio" />
      </div>
      <LiveKitRoom
        serverUrl={livekitUrl}
        token={token}
        connect
        audio
        video={false}
        className="rounded"
      >
        <RoomAudioRenderer />
        <MicToggle />
      </LiveKitRoom>
    </div>
  );
}
