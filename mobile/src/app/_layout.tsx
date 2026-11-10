import { Barlow_400Regular, Barlow_500Medium, Barlow_600SemiBold, useFonts } from '@expo-google-fonts/barlow'
import { BarlowSemiCondensed_600SemiBold } from '@expo-google-fonts/barlow-semi-condensed'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'

import { FONTES, useCores } from '@/tema'

SplashScreen.preventAutoHideAsync()

export default function Layout() {
  const c = useCores()
  // useFonts em vez do plugin de fontes: o plugin não funciona no Expo Go nem na web.
  const [carregadas, erro] = useFonts({
    Barlow_400Regular,
    Barlow_500Medium,
    Barlow_600SemiBold,
    BarlowSemiCondensed_600SemiBold,
  })

  useEffect(() => {
    if (carregadas || erro) SplashScreen.hideAsync()
  }, [carregadas, erro])

  if (!carregadas && !erro) return null

  return (
    <>
      <StatusBar style="auto" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: c.tela },
          headerTintColor: c.tinta,
          headerTitleStyle: { fontFamily: FONTES.forte },
          headerLargeTitleStyle: { fontFamily: FONTES.numero },
          headerShadowVisible: false,
          headerBackButtonDisplayMode: 'minimal',
          contentStyle: { backgroundColor: c.tela },
        }}
      >
        <Stack.Screen name="index" options={{ title: 'Radar Energisa', headerLargeTitleEnabled: true }} />
        <Stack.Screen name="mencao/[id]" options={{ title: '' }} />
      </Stack>
    </>
  )
}
