import { addLocale, setDefaultLang, useLocale } from 'ttag'
import catalog from './en.json'

setDefaultLang('en')
addLocale('en', catalog)
useLocale('en')
