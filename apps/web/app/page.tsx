import GameClient from "./components/game-client";

export const metadata = {
  title: "灰幕 · AI 跑团",
  description: "一个会记住选择、遵守规则并缓慢推进的 AI 跑团世界。",
};

export default function Home() {
  return <GameClient />;
}
