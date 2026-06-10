import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import Navbar from './components/Navbar'
import Hero from './components/Hero'
import WhatSection from './components/WhatSection'
import ToolsGrid from './components/ToolsGrid'
import SetupSection from './components/SetupSection'
import OutroSection from './components/OutroSection'
import Footer from './components/Footer'

gsap.registerPlugin(ScrollTrigger)

function NoiseOverlay() {
  return (
    <svg
      aria-hidden="true"
      style={{
        position: 'fixed', inset: 0,
        width: '100%', height: '100%',
        pointerEvents: 'none', zIndex: 9999, opacity: 0.05,
      }}
    >
      <filter id="grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.65" numOctaves="3" stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect width="100%" height="100%" filter="url(#grain)" />
    </svg>
  )
}

export default function App() {
  return (
    <>
      <NoiseOverlay />
      <Navbar />
      <main>
        <Hero />
        <WhatSection />
        <ToolsGrid />
        <SetupSection />
        <OutroSection />
      </main>
      <Footer />
    </>
  )
}
