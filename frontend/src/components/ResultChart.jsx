import ReactECharts from "echarts-for-react";

/**
 * =========================================================================
 * ECharts 智能图表组件
 * =========================================================================
 *
 * 职责:
 *   根据 chartDetector 推断出的图表配置，使用 ECharts 渲染柱状图或折线图。
 *
 * 设计思路:
 *   - 接收 { chartType, xAxis, yAxis } 配置，而非让组件自己猜测
 *   - 柱状图使用圆角顶部 + 阴影提示，折线图使用平滑曲线 + 十字准星
 *   - X 轴标签过长时自动旋转
 *   - 支持多 Y 轴系列（如同时展示 sales_volume 和 total_amount）
 *   - 图表标题根据列名动态生成，语义化
 *
 * Props:
 *   @param {string[]} columns     - 列名列表
 *   @param {any[][]}   data        - 二维数据数组
 *   @param {{ chartType, xAxis, yAxis }} chartConfig - 图表配置
 * =========================================================================
 */

// ── 动态生成图表标题 ──
function generateChartTitle(chartType, xAxis, yAxis) {
  const yLabel = yAxis.join(" & ");
  const xLabel = xAxis || "分类";

  if (chartType === "line") {
    return `${yLabel} 趋势图`;
  }
  // 柱状图
  const barTemplates = [
    `${yLabel} 对比`,
    `各${xLabel}的${yLabel}`,
    `${yLabel} 分布`,
  ];
  // 如果 xLabel 看起来像中文且有实际含义，用模板2
  if (/[一-龥]/.test(xLabel) && xLabel.length <= 10) {
    return barTemplates[1];
  }
  return barTemplates[0];
}

export default function ResultChart({ columns, data, chartConfig }) {
  // ── 边界条件：无数据时不渲染图表 ──
  if (!columns || columns.length === 0 || !data || data.length === 0) {
    return null;
  }

  const { chartType, xAxis, yAxis } = chartConfig || {};
  if (!chartType || !yAxis?.length) return null;

  // 找到 X 轴列在 columns 中的索引
  const xAxisIndex = columns.indexOf(xAxis);
  if (xAxisIndex === -1) return null;

  // 提取 X 轴标签数据
  const xLabels = data.map((row) => String(row[xAxisIndex] ?? ""));

  // 构建 Y 轴系列（支持多指标）
  const seriesList = yAxis.map((yColName) => {
    const yIndex = columns.indexOf(yColName);
    if (yIndex === -1) return null;

    return {
      name: yColName,
      type: chartType,
      data: data.map((row) => {
        const val = row[yIndex];
        // 兼容字符串形式的数值
        return typeof val === "string" ? parseFloat(val) : val;
      }),
      // 样式细节
      itemStyle: {
        borderRadius: chartType === "bar" ? [6, 6, 0, 0] : undefined,
      },
      smooth: chartType === "line",
      emphasis: {
        focus: "series",
      },
    };
  }).filter(Boolean);

  const manyPoints = xLabels.length > 30;

  // ── ECharts 配置 ──
  const chartOption = {
    // 标题 — 动态生成
    title: {
      text: generateChartTitle(chartType, xAxis, yAxis),
      left: "center",
      textStyle: {
        fontSize: 15,
        fontWeight: "normal",
        color: "#888",
      },
    },

    // 提示框
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: chartType === "line" ? "cross" : "shadow",
        crossStyle: { color: "#999" },
      },
      valueFormatter: (value) => {
        if (typeof value === "number") {
          return value.toLocaleString("zh-CN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          });
        }
        return value;
      },
    },

    // 图例
    legend: {
      data: yAxis,
      bottom: 5,
      textStyle: { fontSize: 12 },
    },

    // 绘图网格
    grid: {
      left: "5%",
      right: "5%",
      bottom: manyPoints ? "22%" : "12%",
      top: "15%",
      containLabel: true,
    },

    // 数据点较多时提供缩放条，而不是强行挤下所有标签
    dataZoom: manyPoints
      ? [
          { type: "inside", start: 0, end: Math.min(100, (30 / xLabels.length) * 100) },
          { type: "slider", bottom: 30, height: 16 },
        ]
      : undefined,

    // X 轴（类目轴）
    xAxis: {
      type: "category",
      data: xLabels,
      name: xAxis,
      nameLocation: "middle",
      nameGap: 35,
      axisLabel: {
        rotate: xLabels.length > 6 ? 30 : 0,
        fontSize: 11,
        interval: manyPoints ? "auto" : 0, // 点少时显示所有标签
      },
    },

    // Y 轴（数值轴）
    yAxis: {
      type: "value",
      name: yAxis.join(" / "),
      nameTextStyle: { fontSize: 11 },
      axisLabel: {
        formatter: (value) => {
          // 大数字格式化（万、千）
          if (Math.abs(value) >= 10000) {
            return (value / 10000).toFixed(1) + "万";
          }
          if (Math.abs(value) >= 1000) {
            return (value / 1000).toFixed(1) + "k";
          }
          return value;
        },
      },
    },

    // 系列
    series: seriesList,

    // 调色盘
    color: ["#667eea", "#764ba2", "#f093fb", "#4facfe", "#43e97b"],
  };

  return (
    <div className="chart-section">
      <h3>{chartType === "line" ? "📈 折线图" : "📊 柱状图"}</h3>
      <ReactECharts
        option={chartOption}
        style={{ height: "420px", width: "100%" }}
        notMerge={true}
        lazyUpdate={true}
      />
    </div>
  );
}
