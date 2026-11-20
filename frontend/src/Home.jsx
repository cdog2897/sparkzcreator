export default function Home({ tools, onOpenTool }) {
  return (
    <div className="home-shell">
      <header className="topbar">
        <div className="brand">
          <SparkMark />
          <h1>Sparkz Creator</h1>
        </div>
      </header>

      <main className="home-main">
        <div className="home-intro">
          <p className="home-kicker">Tools</p>
          <h2>What do you want to make?</h2>
          <p>Pick a tool to start. New video formats and tools will show up here.</p>
        </div>

        <ul className="tool-grid">
          {tools.map((tool) => {
            const Icon = tool.icon;
            return (
              <li key={tool.id}>
                <a
                  className="tool-card"
                  href={tool.route}
                  onClick={(event) => {
                    if (
                      event.metaKey
                      || event.ctrlKey
                      || event.shiftKey
                      || event.altKey
                      || event.button !== 0
                    ) {
                      return;
                    }
                    event.preventDefault();
                    onOpenTool(tool);
                  }}
                >
                  <span className="tool-card-icon" aria-hidden="true">
                    <Icon size={22} strokeWidth={2.1} />
                  </span>
                  <span className="tool-card-body">
                    <strong>{tool.name}</strong>
                    <span>{tool.description}</span>
                  </span>
                  <span className="tool-card-action">
                    Open
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      </main>
    </div>
  );
}

function SparkMark() {
  return (
    <svg aria-hidden="true" className="spark-mark" height="32" viewBox="0 0 32 32" width="32">
      <rect fill="#172033" height="32" rx="8" width="32" />
      <path d="M13.2 10.2v11.6l9.4-5.8-9.4-5.8z" fill="#f97316" />
      <circle cx="23.6" cy="8.8" fill="#7dd3fc" r="1.5" />
    </svg>
  );
}
