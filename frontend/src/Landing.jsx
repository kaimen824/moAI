import { useEffect } from 'react'
import './landing.css'

/* 滚动浮现:IntersectionObserver(禁止 scroll 监听) */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll('.l-reveal')
    const io = new IntersectionObserver(
      entries => entries.forEach(e => e.isIntersecting && e.target.classList.add('in')),
      { amount: 0.25 },
    )
    els.forEach(el => io.observe(el))
    return () => io.disconnect()
  }, [])
}

export default function Landing({ onEnter }) {
  useReveal()

  return (
    <div className="landing">
      <nav className="l-nav l-wrap">
        <div className="l-logo">墨<em>澜</em></div>
        <div className="l-nav-links">
          <a href="#flow">工作方式</a>
          <a href="#features">能力</a>
          <a href="#evidence">实测</a>
        </div>
        <button className="l-cta" onClick={onEnter}>进入工作台</button>
      </nav>

      <header className="l-hero l-wrap">
        <div className="l-hero-copy">
          <h1>与 AI 合写<br />一部<em>不断线</em>的长篇</h1>
          <p>六个 AI 角色协同分工,替你记住每个角色的秘密、每条伏笔、每次反转。写到第三百章,世界依然严丝合缝。</p>
          <button className="l-cta" onClick={onEnter}>进入工作台</button>
        </div>

        {/* 真实组件预览:阅读器 mini 版(工作台同款排版的静态节选) */}
        <div className="l-preview" aria-label="阅读界面预览">
          <div className="pv-title">第 41 章 · 雨夜来客</div>
          <div className="pv-body">
            沈砚推开柴门,雨水顺着蓑衣淌了一地。他不知道的是,
            <span className="pv-pov" title="此信息仅读者与白芷视角可知,主角沈砚并不知情">那封信三日前就已被人拆开重封</span>。
            灯下,白芷抬起头,神色如常。
          </div>
          <div className="pv-note">
            <b>视角隔离</b> 主角不知道的,他的戏份里就不会出现。系统按角色所见过滤记忆,防止剧透渗漏。
          </div>
        </div>
      </header>

      <section className="l-section l-wrap" id="flow">
        <h2 className="l-reveal">三种身份,合著一书</h2>
        <div className="l-steps">
          <div className="l-step l-reveal">
            <h3>共创世界观</h3>
            <p>和主控 AI 聊出基调、核心冲突与人物。你确认总大纲后,故事的骨架就此定音。</p>
          </div>
          <div className="l-step l-reveal">
            <h3>逐章生成</h3>
            <p>细纲、正文、双评审流水线自动运转。写得不好,AI 自己打回重写,到你眼前的已是校订稿。</p>
          </div>
          <div className="l-step l-reveal">
            <h3>掌舵定稿</h3>
            <p>大纲、成稿、伏笔,三个关口都等你点头。不满意就下修改意见,章与人机之间随时往复。</p>
          </div>
        </div>
      </section>

      <section className="l-section l-wrap" id="features">
        <h2 className="l-reveal">支撑长篇的<em>记忆工程</em></h2>
        <div className="l-bento">
          <div className="l-cell span-2 memory l-reveal">
            <div className="glyph">卷</div>
            <h3>全书事实库</h3>
            <p>每个事件、每条设定、每个角色的认知都结构化入库,带版本链。第三十章推翻第一章的真相?记下来,且任意时点可回放当时的世界状态。</p>
          </div>
          <div className="l-cell tall texture l-reveal">
            <h3>IF 线番外</h3>
            <p>定稿章节可 fork 出一条「如果当时」的平行线,主线不受干扰,番外自由生长。</p>
          </div>
          <div className="l-cell l-reveal">
            <h3>伏笔台账</h3>
            <p>埋设、推进、回收全程登记。悬置过久,审校会提醒你收线。</p>
          </div>
          <div className="l-cell l-reveal">
            <h3>分级用模</h3>
            <p>规划与审校用强模型,抽取与摘要用轻模型。成本花在刀刃上。</p>
          </div>
        </div>
      </section>

      <section className="l-section l-wrap" id="evidence">
        <h2 className="l-reveal">实测,而非宣称</h2>
        <div className="l-evidence">
          <div className="l-reveal">
            <div className="l-ev-num">100<small>%</small></div>
            <div className="l-ev-cap"><b>远距离事实召回</b>。对照组(最近三章作上下文)为 0%:窗口外的信息对它不存在。</div>
          </div>
          <div className="l-reveal">
            <div className="l-ev-num">0<small>%</small></div>
            <div className="l-ev-cap"><b>视角泄漏率</b>。对照组约 65%:把整章原文塞给模型,角色不该知道的信息大量渗入。</div>
          </div>
          <div className="l-reveal">
            <div className="l-ev-num">10-60<small>章</small></div>
            <div className="l-ev-cap"><b>规模稳定</b>。同一套记忆系统在 10 到 60 章的合成世界上,召回不随篇幅衰减。</div>
          </div>
        </div>
        <div className="l-method">数据来自本项目内置评测(合成世界、固定种子、可见性感知标注),脚本随源码发布,可复现。</div>
      </section>

      <section className="l-final l-wrap">
        <h2 className="l-reveal">下一个故事,今晚开卷</h2>
        <p className="l-reveal">书名想好了吗?剩下的交给墨澜。</p>
        <button className="l-cta l-reveal" onClick={onEnter}>进入工作台</button>
      </section>

      <footer className="l-footer l-wrap">
        <div>墨澜 MoLan · 多 Agent 小说合写系统</div>
        <div>LangGraph · FastAPI · React</div>
      </footer>
    </div>
  )
}
