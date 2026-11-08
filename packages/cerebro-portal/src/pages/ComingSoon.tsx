import { PageHeader } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';

export function ComingSoon({ title }: { title: string }) {
  const { t } = useTranslation();
  return <PageHeader title={title} description={t('comingSoon')} />;
}
