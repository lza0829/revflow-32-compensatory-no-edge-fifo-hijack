<script setup>
</script>
<template>
  <div class="desk">
    <aside class="rail">
      <div class="rail-brand">
        <span class="rail-mark">RF</span>
        <div class="rail-name">
          <strong>冲流台</strong>
          <em>Revflow</em>
        </div>
      </div>

      <nav class="rail-nav">
        <router-link to="/"><span class="idx">01</span>总览</router-link>
        <router-link to="/accounts"><span class="idx">02</span>账户</router-link>
        <router-link to="/forward"><span class="idx">03</span>正向流水</router-link>
        <router-link to="/reversal"><span class="idx">04</span>冲正流水</router-link>
        <router-link to="/settle"><span class="idx">05</span>归并台</router-link>
        <router-link to="/quarantine"><span class="idx">06</span>未平隔离</router-link>
        <router-link to="/verify"><span class="idx">07</span>净额互证</router-link>
      </nav>

      <div class="rail-note">
        <p>正 → 冲 → 净</p>
        <p>本地夹具 · 10700</p>
      </div>
    </aside>

    <section class="work">
      <div class="ledger-bg" aria-hidden="true"></div>
      <header class="work-head">
        <p class="work-kicker">流水净额工作台</p>
        <div class="work-meta">
          <span>装载</span>
          <span>配平</span>
          <span>过账</span>
        </div>
      </header>
      <div class="work-body">
        <router-view />
      </div>
    </section>
  </div>
</template>
