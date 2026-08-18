import React from 'react';
import { StatusBar } from 'react-native';
import HomeScreen from './src/screens/HomeScreen';

export default function App(): JSX.Element {
  return (
    <>
      <StatusBar barStyle="light-content" />
      <HomeScreen />
    </>
  );
}
