import * as echarts from "echarts";

/** 与界面主色一致的 ECharts 主题，Agent 图表和 SQL 查询图表共用 */
export const CHART_THEME = "agent";
export const CHART_PALETTE = ["#6c5ce7", "#22b8cf", "#f59f00", "#ff6b9a", "#20c997", "#845ef7", "#4dabf7"];

echarts.registerTheme(CHART_THEME, {
  color: CHART_PALETTE,
  backgroundColor: "transparent",
  textStyle: { fontFamily: "inherit", color: "#5b5a72" },
  title: { textStyle: { color: "#17162a", fontWeight: 600 } },
  legend: { textStyle: { color: "#5b5a72" }, icon: "roundRect", itemWidth: 10, itemHeight: 10 },
  tooltip: {
    backgroundColor: "rgba(255,255,255,0.96)",
    borderColor: "#e9e8f2",
    textStyle: { color: "#17162a" },
    extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(23,22,42,.08);",
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "#e3e2ee" } },
    axisTick: { show: false },
    axisLabel: { color: "#8584a0" },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisLabel: { color: "#8584a0" },
    splitLine: { lineStyle: { color: "#f0eff6", type: "dashed" } },
  },
  bar: { itemStyle: { borderRadius: [6, 6, 0, 0] }, barMaxWidth: 36 },
  line: { symbol: "circle", symbolSize: 6, lineStyle: { width: 3 } },
});
