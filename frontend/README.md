# DataMind 前端

React 19 + Vite + ECharts。项目整体说明见根目录 [README](../README.md)。

## 开发

```bash
npm install
```

```bash
npm run dev
```

开发服务器运行在 `http://localhost:5173`，`/api` 请求代理到 `http://localhost:8000`（见 `vite.config.js`）。

```bash
npm run lint
```

```bash
npm run build
```

生产环境由 `deploy/frontend.Dockerfile` 构建静态资源并用 Nginx 提供服务。

## 目录

```text
src/
  App.jsx                 路由（hash）、登录态、会话与流式对话
  api.js                  API 客户端：鉴权、SSE 事件流解析、管理接口
  components/
    ChatView.jsx          对话页：首屏、对话流
    Composer.jsx          输入框：模式、模型、技能选择
    AssistantAnswer.jsx   智能助手的推理轨迹与回答
    AgentAnswer.jsx       经营分析结果（指标、图表、归因、报告）
    MemoryView.jsx        我的记忆
    wiki/                 知识库：目录树、索引页、阅读、编辑、版本历史、上传
    admin/                管理页：模型、工具、技能
  utils/
    markdown.js           Markdown 渲染（[[双向链接]]、目录、DOMPurify 清洗）
    chartDetector.js      查询结果的图表类型推断
    echartsTheme.js       图表主题
```
