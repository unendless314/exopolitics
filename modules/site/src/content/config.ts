import { defineCollection, z } from 'astro:content';

const postsCollection = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    publishDate: z.coerce.date(),
    description: z.string(),
    canonicalUrl: z.string().nullable().optional(),
    disclosureNote: z.string().nullable().optional(),
    authorMetadata: z.object({
      source_module: z.string().optional(),
      writer_type: z.string().optional(),
      editor: z.string().optional(),
      upstream_updated_at: z.string().optional()
    }).optional()
  })
});

export const collections = {
  posts: postsCollection
};
