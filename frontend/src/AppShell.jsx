import { useEffect } from 'react';
import Home from './Home.jsx';
import { useRoute } from './navigation.js';
import { tools } from './tools.js';

export default function AppShell() {
  const [path, navigate] = useRoute();
  const tool = tools.find((item) => item.route === path) || null;

  useEffect(() => {
    document.title = tool ? `${tool.name} · Sparkz Creator` : 'Sparkz Creator';
  }, [tool]);

  if (tool) {
    const Tool = tool.component;
    return <Tool onHome={() => navigate('/')} />;
  }

  return (
    <Home
      onOpenTool={(item) => navigate(item.route)}
      tools={tools}
    />
  );
}
